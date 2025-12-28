#!/usr/bin/env python3
"""
TCI CW Controller - Main Application

Controls SunSDR radios via TCI protocol with:
- F1-F12 keyboard macros for CW text messages
- USB paddle input (XIAO SAMD21) for manual keying
- Local sidetone for paddle operation
"""

import asyncio
import logging
import signal
import sys
import yaml
from pathlib import Path

from tci_client import TCIClient
from keyboard_handler import KeyboardHandler
from usb_paddle_handler import USBPaddleHandler
from sidetone_generator import SidetoneGenerator


class TCICWController:
    """Main application controller"""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize TCI CW Controller
        
        Args:
            config_path: Path to configuration file
        """
        # Load configuration
        self.config = self._load_config(config_path)
        
        # Setup logging
        self._setup_logging()
        self.logger = logging.getLogger("TCICWController")
        
        # Components
        self.tci_client: TCIClient = None
        self.keyboard_handler: KeyboardHandler = None
        self.usb_paddle_handler: USBPaddleHandler = None
        self.sidetone: SidetoneGenerator = None
        
        # State
        self.running = False
        self.reconnect_task = None
        
        # PTT state tracking for paddle keying
        self.paddle_ptt_active = False
        self.paddle_auto_ptt = self.config['cw'].get('paddle_auto_ptt', True)
        self.paddle_ptt_hangtime = 1.0  # Seconds to keep PTT active after last key-up
        self.paddle_ptt_release_task = None
    
    def _load_config(self, config_path: str) -> dict:
        """Load configuration from YAML file"""
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    
    def _setup_logging(self):
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%H:%M:%S'
        )
    
    async def _initialize_tci(self) -> bool:
        """
        Initialize TCI client and connect to server
        
        Returns:
            True if successful, False otherwise
        """
        tci_config = self.config['tci']
        
        self.logger.info(f"Connecting to TCI server {tci_config['host']}:{tci_config['port']}")
        
        self.tci_client = TCIClient(
            host=tci_config['host'],
            port=tci_config['port'],
            trx_number=tci_config.get('trx_number', 0)
        )
        
        # Setup callbacks
        self.tci_client.on_ready = self._on_tci_ready
        self.tci_client.on_disconnect = self._on_tci_disconnect
        self.tci_client.on_message = self._on_tci_message
        
        # Connect
        success = await self.tci_client.connect()
        
        if success:
            # Set initial radio state
            cw_config = self.config['cw']
            await self.tci_client.set_mode_cw(cw_config.get('default_mode', 'CW'))
            await self.tci_client.set_cw_speed(cw_config.get('speed_wpm', 25))
        
        return success
    
    def _initialize_keyboard(self):
        """Initialize F-key keyboard handler"""
        self.logger.info("Initializing F-key handler")
        
        self.keyboard_handler = KeyboardHandler(
            function_keys=self.config['function_keys'],
            callsign=self.config['operator']['callsign'],
            loop=asyncio.get_event_loop()
        )
        
        # Setup callback
        self.keyboard_handler.on_macro_send = self._on_macro_send
        
        self.keyboard_handler.start()
    
    def _initialize_usb_paddle(self) -> bool:
        """
        Initialize USB paddle handler
        
        Returns:
            True if successful, False if disabled/failed
        """
        usb_config = self.config['usb_hid']
        
        if not usb_config.get('enabled', True):
            self.logger.info("USB paddle input disabled in config")
            return False
        
        self.logger.info("Initializing USB paddle handler")
        
        usb_config = self.config['usb_hid']
        cw_config = self.config['cw']
        
        self.usb_paddle_handler = USBPaddleHandler(
            device_path=usb_config.get('device_path'),
            vendor_id=usb_config.get('vendor_id', '2886'),
            product_id=usb_config.get('product_id', '802f'),
            keyer_mode=cw_config.get('keyer_mode', 'straight'),
            wpm=cw_config.get('speed_wpm', 25),
            debug=usb_config.get('debug', False)
        )
        
        # Connect to USB device
        if not self.usb_paddle_handler.connect():
            self.logger.warning("USB paddle not found - manual keying disabled")
            self.usb_paddle_handler = None
            return False
        
        # Setup callback
        self.usb_paddle_handler.on_key_event = self._on_paddle_event
        
        # Pass sidetone to paddle handler for immediate audio feedback (if enabled)
        if self.sidetone:
            self.usb_paddle_handler.set_sidetone(self.sidetone)
            self.logger.info("Local sidetone enabled for paddle")
        else:
            self.logger.info("Using ExpertSDR3 sidetone only")
        
        return True
    
    def _initialize_sidetone(self) -> bool:
        """
        Initialize local sidetone generator
        
        Returns:
            True if successful, False if disabled/failed
        """
        sidetone_config = self.config['sidetone']
        
        if not sidetone_config.get('enabled', True):
            self.logger.info("Sidetone disabled in config")
            return False
        
        try:
            self.logger.info(f"Initializing sidetone: {sidetone_config['frequency']} Hz")
            
            self.sidetone = SidetoneGenerator(
                frequency=sidetone_config.get('frequency', 600),
                volume=sidetone_config.get('volume', 0.5)
            )
            
            return True
            
        except Exception as e:
            self.logger.warning(f"Failed to initialize sidetone: {e}")
            self.sidetone = None
            return False
    
    def _on_tci_ready(self):
        """Callback when TCI server is ready"""
        self.logger.info("TCI server ready")
        print("\n" + "="*60)
        print("TCI CW CONTROLLER READY")
        print("="*60)
        print("F1-F12: Send CW macros")
        if self.usb_paddle_handler:
            print("USB Paddle: Manual keying")
        print("Ctrl+C: Quit")
        print("="*60 + "\n")
    
    def _on_tci_disconnect(self):
        """Callback when TCI disconnects"""
        self.logger.warning("TCI disconnected")
        
        # Attempt reconnection if enabled
        if self.config['tci'].get('auto_reconnect', True) and self.running:
            self.logger.info("Scheduling reconnection...")
            if not self.reconnect_task or self.reconnect_task.done():
                self.reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    def _on_tci_message(self, message: str):
        """
        Callback for TCI messages
        
        Args:
            message: TCI message string
        """
        # Log interesting messages
        if message.startswith(('VFO:', 'MODULATION:', 'TRX:')):
            self.logger.debug(f"TCI: {message}")
    
    async def _on_macro_send(self, key_name: str, message: str):
        """
        Callback when F-key macro is pressed (called from pynput thread via run_coroutine_threadsafe)
        
        Args:
            key_name: Function key name (F1-F12)
            message: CW message to send
        """
        if not self.tci_client or not self.tci_client.ready:
            self.logger.warning(f"Cannot send {key_name}: TCI not ready")
            return
        
        self.logger.info(f"Sending CW macro: {message}")
        
        try:
            force_ptt = self.config['tci'].get('force_ptt', False)
            await self.tci_client.send_cw_macros(message, force_ptt=force_ptt)
        except Exception as e:
            self.logger.error(f"Error sending CW macro: {e}")
    
    async def _on_paddle_event(self, key_down: bool, previous_duration_ms: int):
        """
        Callback when USB paddle state changes
        
        Args:
            key_down: Current key state (True=down, False=up)
            previous_duration_ms: Duration of previous state in milliseconds
        
        Note: Sidetone is now handled directly in usb_paddle_handler for precise timing
        """
        # Send to TCI
        if self.tci_client and self.tci_client.ready:
            # Auto-PTT: Activate PTT on first key-down if enabled
            if self.paddle_auto_ptt and key_down and not self.paddle_ptt_active:
                # Cancel any pending PTT release
                if self.paddle_ptt_release_task:
                    self.paddle_ptt_release_task.cancel()
                    self.paddle_ptt_release_task = None
                
                await self.tci_client.set_ptt(True)
                self.paddle_ptt_active = True
                self.logger.debug("Paddle PTT activated")
            
            # Send keyer command
            await self.tci_client.send_keyer(key_down, previous_duration_ms)
            
            # Auto-PTT: Schedule PTT release after key-up with hangtime
            if self.paddle_auto_ptt and not key_down and self.paddle_ptt_active:
                # Cancel any previous release task
                if self.paddle_ptt_release_task:
                    self.paddle_ptt_release_task.cancel()
                
                # Schedule new release after hangtime
                self.paddle_ptt_release_task = asyncio.create_task(
                    self._release_paddle_ptt_after_hangtime()
                )
    
    async def _release_paddle_ptt_after_hangtime(self):
        """Release paddle PTT after hangtime delay"""
        try:
            await asyncio.sleep(self.paddle_ptt_hangtime)
            
            if self.tci_client and self.tci_client.ready and self.paddle_ptt_active:
                await self.tci_client.set_ptt(False)
                self.paddle_ptt_active = False
                self.logger.debug("Paddle PTT released (hangtime)")
        except asyncio.CancelledError:
            # Task was cancelled (new keying started)
            pass
    
    async def _reconnect_loop(self):
        """Reconnection loop with delay"""
        delay = self.config['tci'].get('reconnect_delay', 3.0)
        
        while self.running:
            self.logger.info(f"Reconnecting in {delay} seconds...")
            await asyncio.sleep(delay)
            
            if await self._initialize_tci():
                self.logger.info("Reconnected successfully")
                
                # Restart receive loop
                asyncio.create_task(self.tci_client.receive_loop())
                break
            else:
                self.logger.error("Reconnection failed, retrying...")
    
    async def _keepalive_ping_loop(self):
        """
        Send periodic VFO queries to keep ExpertSDR3 event loop active
        Workaround for Windows message pump not processing WebSocket without UI events
        """
        self.logger.info("Keepalive ping enabled (workaround for ExpertSDR3)")
        
        while self.running:
            try:
                if self.tci_client and self.tci_client.ready:
                    # Send harmless query that requires response
                    await self.tci_client.send_command(f"VFO:{self.tci_client.trx_number},0")
                
                # Ping every 100ms
                await asyncio.sleep(0.1)
                
            except Exception as e:
                self.logger.debug(f"Keepalive ping error: {e}")
                await asyncio.sleep(1.0)
    
    async def _event_loop_wakeup(self):
        """
        Periodically wake up the event loop to process callbacks from threads
        Ensures keyboard callbacks scheduled via run_coroutine_threadsafe are processed
        """
        self.logger.info("Event loop wakeup task started (polls keyboard queue every 10ms)")
        while self.running:
            # Process keyboard queue
            if self.keyboard_handler:
                await self.keyboard_handler.process_queue()
            
            # Yield control - keeps event loop responsive
            await asyncio.sleep(0.01)  # 10ms - fast enough for responsive UI
    
    async def run(self):
        """Main application loop"""
        self.running = True
        
        try:
            # Initialize TCI connection
            if not await self._initialize_tci():
                self.logger.error("Failed to connect to TCI server")
                return 1
            
            # Initialize keyboard handler
            self._initialize_keyboard()
            
            # Initialize sidetone first (before USB paddle)
            self._initialize_sidetone()
            
            # Initialize USB paddle (optional) - needs sidetone to be ready
            self._initialize_usb_paddle()
            
            # Start async tasks
            tasks = []
            
            # TCI receive loop
            tasks.append(asyncio.create_task(self.tci_client.receive_loop()))
            
            # Keepalive ping task (workaround for ExpertSDR3 event loop)
            if self.config['tci'].get('keepalive_ping', False):
                tasks.append(asyncio.create_task(self._keepalive_ping_loop()))
            
            # Event loop wakeup task disabled - was too aggressive
            # tasks.append(asyncio.create_task(self._event_loop_wakeup()))
            
            # USB paddle poll loop
            if self.usb_paddle_handler:
                tasks.append(asyncio.create_task(self.usb_paddle_handler.poll_loop()))
            
            # Wait for all tasks (or until interrupted)
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except asyncio.CancelledError:
            self.logger.info("Application cancelled")
        except Exception as e:
            self.logger.error(f"Application error: {e}", exc_info=True)
            return 1
        finally:
            await self.shutdown()
        
        return 0
    
    async def shutdown(self):
        """Clean shutdown"""
        self.logger.info("Shutting down...")
        self.running = False
        
        # Stop keyboard handler
        if self.keyboard_handler:
            self.keyboard_handler.stop()
        
        # Stop USB paddle
        if self.usb_paddle_handler:
            self.usb_paddle_handler.stop()
        
        # Close sidetone
        if self.sidetone:
            self.sidetone.close()
        
        # Disconnect TCI
        if self.tci_client:
            await self.tci_client.disconnect()
        
        self.logger.info("Shutdown complete")


async def main():
    """Application entry point"""
    # Handle Ctrl+C gracefully
    loop = asyncio.get_event_loop()
    controller = None
    
    def signal_handler(sig, frame):
        """Handle interrupt signal"""
        if controller:
            print("\n\nInterrupt received, shutting down...")
            asyncio.create_task(controller.shutdown())
            # Cancel all tasks
            for task in asyncio.all_tasks(loop):
                task.cancel()
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Run controller
    controller = TCICWController()
    exit_code = await controller.run()
    
    sys.exit(exit_code)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
