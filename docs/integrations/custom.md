# Custom Transports

`python-jsonrpc-lib` is transport-agnostic. `rpc.handle(data)` takes a string or bytes and returns a string — everything else is up to you. This page shows how to wire it to transports beyond HTTP.

## TCP Server

The simplest custom transport: a raw TCP socket. Useful for internal microservice communication, embedded systems, or any scenario where HTTP overhead is unwanted. Each connection sends one request and receives one response.

```python title="tcp_server.py"
import socket
from dataclasses import dataclass
from jsonrpc import JSONRPC, Method

# Define method
@dataclass
class EchoParams:
    message: str

class Echo(Method):
    def execute(self, params: EchoParams) -> str:
        """Echo back the message."""
        return f"Echo: {params.message}"

# Setup RPC
rpc = JSONRPC(version='2.0')
rpc.register('echo', Echo())

# TCP server
def start_tcp_server(host='127.0.0.1', port=9000):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(5)
        print(f"JSON-RPC TCP server listening on {host}:{port}")

        while True:
            client, address = server.accept()
            print(f"Connection from {address}")

            with client:
                # Receive request
                data = client.recv(4096)
                if not data:
                    continue

                # Handle JSON-RPC
                response = rpc.handle(data.decode('utf-8'))

                # Send response
                client.sendall(response.encode('utf-8'))

if __name__ == '__main__':
    start_tcp_server()
```

**Test with netcat:**

```bash
echo '{"jsonrpc": "2.0", "method": "echo", "params": {"message": "Hello"}, "id": 1}' | nc 127.0.0.1 9000
```

**Response:**

```json
{"jsonrpc": "2.0", "result": "Echo: Hello", "id": 1}
```

## Async TCP Server

The async version uses `asyncio.start_server` and `rpc.handle_async()`. It handles many concurrent connections without threads — better for high-load internal services.

```python title="async_tcp_server.py"
import asyncio
from jsonrpc import JSONRPC, Method

rpc = JSONRPC(version='2.0')
# ... register methods ...

async def handle_client(reader, writer):
    addr = writer.get_extra_info('peername')
    print(f"Connection from {addr}")

    try:
        # Read request
        data = await reader.read(4096)
        message = data.decode('utf-8')

        # Handle async
        response = await rpc.handle_async(message)

        # Write response
        writer.write(response.encode('utf-8'))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()

async def start_server(host='127.0.0.1', port=9000):
    server = await asyncio.start_server(handle_client, host, port)
    addr = server.sockets[0].getsockname()
    print(f"Async TCP server on {addr}")

    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(start_server())
```

## WebSocket Server (Pure)

WebSocket provides full-duplex communication over a single connection. This is the right choice when clients need to send multiple requests without reconnecting each time — dashboards, live feeds, interactive tools.

The `websockets` library handles the WebSocket protocol; `rpc.handle_async()` handles the JSON-RPC layer.

```python title="websocket_server.py"
import asyncio
import websockets
from jsonrpc import JSONRPC, Method

rpc = JSONRPC(version='2.0')
# ... register methods ...

async def handle_websocket(websocket, path):
    print(f"WebSocket connection: {websocket.remote_address}")

    try:
        async for message in websocket:
            # Handle JSON-RPC
            response = await rpc.handle_async(message)

            # Send response
            await websocket.send(response)
    except websockets.exceptions.ConnectionClosed:
        print("WebSocket connection closed")

async def start_server(host='localhost', port=9001):
    async with websockets.serve(handle_websocket, host, port):
        print(f"WebSocket server on ws://{host}:{port}")
        await asyncio.Future()  # run forever

if __name__ == '__main__':
    asyncio.run(start_server())
```

**Client example:**

```python title="websocket_client.py"
import asyncio
import websockets
import json

async def call_rpc():
    uri = "ws://localhost:9001"
    async with websockets.connect(uri) as websocket:
        # Send request
        request = {
            "jsonrpc": "2.0",
            "method": "echo",
            "params": {"message": "Hello WebSocket"},
            "id": 1
        }
        await websocket.send(json.dumps(request))

        # Receive response
        response = await websocket.recv()
        print(f"Response: {response}")

asyncio.run(call_rpc())
```

## Redis Queue

Redis lists work as a lightweight job queue. A producer pushes JSON-RPC requests to one list; the worker pops them, processes them with `rpc.handle()`, and pushes results to another list. Useful when callers and handlers run in separate processes and don't need a persistent connection.

```python title="redis_worker.py"
import redis
from jsonrpc import JSONRPC, Method

rpc = JSONRPC(version='2.0')
# ... register methods ...

r = redis.Redis(host='localhost', port=6379, db=0)

def process_queue():
    print("Redis RPC worker started")

    while True:
        # Block until request available
        _, request = r.brpop('rpc_requests')

        # Handle JSON-RPC
        response = rpc.handle(request.decode('utf-8'))

        # Push response
        r.lpush('rpc_responses', response)

if __name__ == '__main__':
    process_queue()
```

## MQTT

MQTT is a publish/subscribe protocol common in IoT and embedded systems. Subscribe to a request topic, publish responses to a response topic. The broker decouples senders from receivers — the RPC handler doesn't need to know who sent the request.

```python title="mqtt_broker.py"
import paho.mqtt.client as mqtt
from jsonrpc import JSONRPC, Method

rpc = JSONRPC(version='2.0')
# ... register methods ...

def on_message(client, userdata, msg):
    # Handle JSON-RPC request
    response = rpc.handle(msg.payload.decode('utf-8'))

    # Publish response
    client.publish('rpc/responses', response)

# Connect to MQTT broker
client = mqtt.Client()
client.on_message = on_message
client.connect('localhost', 1883, 60)

# Subscribe to requests
client.subscribe('rpc/requests')

print("MQTT RPC broker started")
client.loop_forever()
```

## Command Line Interface

`rpc.handle()` reads from stdin and writes to stdout — no server, no sockets. Useful for scripts, testing, shell pipelines, or wrapping RPC methods as CLI tools.

```python title="cli_rpc.py"
from jsonrpc import JSONRPC, Method
import json
import sys

rpc = JSONRPC(version='2.0')
# ... register methods ...

def main():
    # Read JSON-RPC request from stdin
    request = sys.stdin.read()

    # Handle request
    response = rpc.handle(request)

    # Write response to stdout
    print(response)

if __name__ == '__main__':
    main()
```

**Usage:**

```bash
echo '{"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1}' | python cli_rpc.py
```

## HTTP Client (requests)

A thin helper for calling a JSON-RPC endpoint from Python. Useful when writing tests, scripts, or internal tooling that needs to call your own or a third-party JSON-RPC API.

```python title="http_client.py"
import requests
import json

def call_rpc(url, method, params, request_id=1):
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": request_id
    }

    response = requests.post(url, json=payload)
    return response.json()

# Example
result = call_rpc(
    "http://localhost:5000/rpc",
    "math.add",
    {"a": 10, "b": 5}
)
print(result['result'])  # 15
```

## IPC (Inter-Process Communication)

Unix domain sockets connect processes on the same machine without going through the network stack — lower latency than TCP, no exposed port. Useful for sidecar processes, plugin architectures, or separating a CPU-intensive worker from a web process.

```python title="ipc_server.py"
import socket
import os
from jsonrpc import JSONRPC, Method

rpc = JSONRPC(version='2.0')
# ... register methods ...

SOCKET_PATH = '/tmp/jsonrpc.sock'

# Remove existing socket
try:
    os.unlink(SOCKET_PATH)
except OSError:
    pass

# Create Unix domain socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
    server.bind(SOCKET_PATH)
    server.listen(1)
    print(f"IPC server listening on {SOCKET_PATH}")

    while True:
        conn, _ = server.accept()
        with conn:
            data = conn.recv(4096)
            response = rpc.handle(data.decode('utf-8'))
            conn.sendall(response.encode('utf-8'))
```

**Client:**

```python title="ipc_client.py"
import socket

SOCKET_PATH = '/tmp/jsonrpc.sock'

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect(SOCKET_PATH)

    request = '{"jsonrpc": "2.0", "method": "echo", "params": {"message": "IPC"}, "id": 1}'
    client.sendall(request.encode('utf-8'))

    response = client.recv(4096)
    print(response.decode('utf-8'))
```

## Key Points

- **Transport-agnostic**: `rpc.handle()` takes a string, returns a string — the transport is always your code
- **Sync or async**: `rpc.handle()` for blocking transports, `rpc.handle_async()` for asyncio-based ones
- **Same methods everywhere**: register once, expose over any number of transports simultaneously

## What's Next?

→ [Advanced Topics](../advanced/async.md) - Async methods, batch, protocols
