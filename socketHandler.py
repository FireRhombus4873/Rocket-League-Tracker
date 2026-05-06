import json
import time
import threading
import socket

HOST = "localhost"
PORT = 49123  # Rocket League Stats API port

class SocketHandler():
    def __init__(self, on_message_callback=None, on_update_state_callback=None,
                 on_status_callback=None):
        self.on_message_callback = on_message_callback
        self.on_update_state_callback = on_update_state_callback
        self.on_status_callback = on_status_callback
        self._thread = None
        self._running = False

    def start(self):
        """Start the listener in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self.listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _emit_status(self, msg: str):
        if self.on_status_callback:
            self.on_status_callback(msg)

    def listen(self):
        decoder = json.JSONDecoder()
        while self._running:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(5)
                    s.connect((HOST, PORT))
                    print(f"Connected to Stats API at {HOST}:{PORT}")
                    self._emit_status("Connected — waiting for match")
                    buffer = ""
                    while self._running:
                        try:
                            chunk = s.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            print("Connection closed, reconnecting...")
                            break
                        buffer += chunk.decode("utf-8", errors="replace")
                        # Pull out as many complete JSON objects as are buffered.
                        # raw_decode stops at the end of one object and reports
                        # how far it got, so back-to-back messages parse cleanly.
                        while buffer:
                            buffer = buffer.lstrip()
                            if not buffer:
                                break
                            try:
                                obj, idx = decoder.raw_decode(buffer)
                            except json.JSONDecodeError:
                                break  # incomplete — wait for more data
                            self._handle_message(obj)
                            buffer = buffer[idx:]
            except (ConnectionRefusedError, socket.timeout):
                self._emit_status("Rocket League detected — waiting for plugin...")
            except Exception as e:
                print(f"Socket error: {e}")
                self._emit_status(f"Socket error: {e}")
            if self._running:
                time.sleep(2)

    def _handle_message(self, outer: dict):
        """
        Stats API message format:
        {
            "Event": "BallHit",          <- PascalCase, no prefix
            "Data": "{...}"              <- Data is a JSON-encoded STRING
        }
        Special event "UpdateState" carries full game snapshot and is
        routed to a separate callback for state management.
        """
        event = outer.get("Event", "Unknown")
        data_raw = outer.get("Data", "{}")
        # Data field is a JSON string — decode it
        data = json.loads(data_raw) if isinstance(data_raw, str) else data_raw

        if event == "UpdateState":
            if self.on_update_state_callback:
                self.on_update_state_callback(data)
        elif self.on_message_callback:
            self.on_message_callback(event, data)
