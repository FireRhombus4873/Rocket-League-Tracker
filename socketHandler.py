import json
import time
import threading
import socket

HOST = "localhost"
PORT = 49123  # Rocket League Stats API port

class SocketHandler():
    def __init__(self, on_message_callback=None, on_update_state_callback=None):
        self.on_message_callback = on_message_callback
        self.on_update_state_callback = on_update_state_callback
        self._thread = None
        self._running = False

    def start(self):
        """Start the listener in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self.listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def listen(self):
        while self._running:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.connect((HOST, PORT))
                    print(f"Connected to Stats API at {HOST}:{PORT}")
                    buffer = b""
                    while self._running:
                        chunk = s.recv(4096)
                        if not chunk:
                            print("Connection closed, reconnecting...")
                            break
                        buffer += chunk
                        # Try to parse whatever has accumulated as a complete JSON message
                        try:
                            message = buffer.decode("utf-8", errors="replace")
                            json.loads(message)          # raises if incomplete
                            self._handle_message(message)
                            buffer = b""
                        except json.JSONDecodeError:
                            pass                         # wait for more data
            except ConnectionRefusedError:
                pass
            except Exception as e:
                print(f"Socket error: {e}")
            if self._running:
                time.sleep(2)

    def _handle_message(self, raw: str):
        """
        Stats API message format:
        {
            "Event": "BallHit",          <- PascalCase, no prefix
            "Data": "{...}"              <- Data is a JSON-encoded STRING
        }
        Special event "UpdateState" carries full game snapshot and is
        routed to a separate callback for state management.
        """
        try:
            outer = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"Failed to parse message: {e}")
            return

        event = outer.get("Event", "Unknown")
        data_raw = outer.get("Data", "{}")
        # Data field is a JSON string — decode it
        data = json.loads(data_raw) if isinstance(data_raw, str) else data_raw

        if event == "UpdateState":
            if self.on_update_state_callback:
                self.on_update_state_callback(data)
        elif self.on_message_callback:
            self.on_message_callback(event, data)