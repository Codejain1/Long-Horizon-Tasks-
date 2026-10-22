#!/usr/bin/env python3
"""Lets the iPhone (same Wi-Fi) reach the bot: LAN 0.0.0.0:8771 -> 127.0.0.1:8770 (the SSM tunnel to the server's
app API). Every request still needs the secret key; nothing on the server is opened to the internet.

    python3 app/dev/relay.py [listen_port] [target_port]
"""
import asyncio
import sys

LISTEN, TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 8771, int(sys.argv[2]) if len(sys.argv) > 2 else 8770


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    finally:
        w.close()


async def handle(cr, cw):
    try:
        tr, tw = await asyncio.open_connection("127.0.0.1", TARGET)
    except OSError:
        cw.close()  # tunnel not up yet
        return
    await asyncio.gather(pipe(cr, tw), pipe(tr, cw), return_exceptions=True)


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN)
    print(f"relay: 0.0.0.0:{LISTEN} -> 127.0.0.1:{TARGET}", flush=True)
    async with server:
        await server.serve_forever()

asyncio.run(main())
