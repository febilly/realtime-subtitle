import asyncio
from osc_manager import osc_manager

async def main():
    await osc_manager.start_server()
    await asyncio.sleep(0.8)
    await osc_manager.stop_server()

asyncio.run(main())
