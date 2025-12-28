#!/usr/bin/env python3
"""
Simple TCI connection tester
Tests both plain TCP and WebSocket connections
"""

import socket
import sys

def test_tcp(host, port, timeout=3):
    """Test plain TCP connection"""
    print(f"Testing TCP connection to {host}:{port}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        print(f"✓ TCP connection successful!")
        
        # Try to receive any data
        sock.settimeout(1.0)
        try:
            data = sock.recv(1024)
            if data:
                print(f"✓ Received data: {data[:100]}")
        except socket.timeout:
            print("  (No immediate data received, but connection established)")
        
        sock.close()
        return True
    except socket.timeout:
        print(f"✗ Connection timeout - port might be filtered/closed")
        return False
    except ConnectionRefusedError:
        print(f"✗ Connection refused - nothing listening on this port")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def test_websocket(host, port):
    """Test WebSocket connection"""
    import asyncio
    import websockets
    
    async def connect():
        uri = f"ws://{host}:{port}"
        print(f"\nTesting WebSocket connection to {uri}...")
        try:
            async with asyncio.timeout(3):
                ws = await websockets.connect(uri)
                print(f"✓ WebSocket connection successful!")
                
                # Try to receive data
                try:
                    async with asyncio.timeout(2):
                        msg = await ws.recv()
                        print(f"✓ Received: {msg[:100]}")
                except asyncio.TimeoutError:
                    print("  (No immediate data, but connected)")
                
                await ws.close()
                return True
        except asyncio.TimeoutError:
            print(f"✗ WebSocket connection timeout")
            return False
        except Exception as e:
            print(f"✗ WebSocket error: {e}")
            return False
    
    return asyncio.run(connect())

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 test_tci_connection.py <host> <port>")
        print("Example: python3 test_tci_connection.py 192.168.1.22 50001")
        sys.exit(1)
    
    host = sys.argv[1]
    port = int(sys.argv[2])
    
    print("="*60)
    print("TCI Connection Tester")
    print("="*60)
    
    # Test TCP first
    tcp_ok = test_tcp(host, port)
    
    # Test WebSocket
    ws_ok = test_websocket(host, port)
    
    print("\n" + "="*60)
    print("Summary:")
    print(f"  TCP: {'✓ Works' if tcp_ok else '✗ Failed'}")
    print(f"  WebSocket: {'✓ Works' if ws_ok else '✗ Failed'}")
    print("="*60)
    
    if not tcp_ok and not ws_ok:
        print("\nTroubleshooting:")
        print("1. Check ExpertSDR3 is running on the target host")
        print("2. Check TCI is enabled in ExpertSDR3 settings")
        print("3. Verify the correct port number")
        print("4. Check firewall settings")
        print("5. Try common ports: 40001, 50001")
