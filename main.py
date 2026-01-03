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
            # Query current state (mode, drive) - responses will be parsed by _on_tci_message
            await self.tci_client.send_command(f"MODULATION:{self.tci_client.trx_number}")
            await self.tci_client.send_command(f"DRIVE:{self.tci_client.trx_number}")
            await asyncio.sleep(0.1)  # Allow responses to arrive
            
            self.logger.info(f"Initial state: mode={self.tci_client.current_mode}, drive={self.tci_client.drive_level}%")
            
            # Set CW mode and speed
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
        vail_config = self.config.get('vail_adapter', {})
        use_vail_firmware = vail_config.get('enabled', False)
        
        self.usb_paddle_handler = USBPaddleHandler(
            device_path=usb_config.get('device_path'),
            vendor_id=usb_config.get('vendor_id', '2886'),
            product_id=usb_config.get('product_id', '802f'),
            keyer_mode=cw_config.get('keyer_mode', 'straight'),
            wpm=cw_config.get('speed_wpm', 25),
            debug=usb_config.get('debug', False),
            use_vail_firmware=use_vail_firmware
        )
        
        # Connect to USB device
        if not self.usb_paddle_handler.connect():
            self.logger.warning("USB paddle not found - manual keying disabled")
            self.usb_paddle_handler = None
            return False
        
        # Configure Vail adapter firmware if enabled
        if use_vail_firmware:
            self._configure_vail_adapter(vail_config)
        
        # Setup callbacks
        self.usb_paddle_handler.on_key_event = self._on_paddle_event
        self.usb_paddle_handler.on_tx_start = self._on_paddle_tx_start
        
        # Pass sidetone to paddle handler for immediate audio feedback (if enabled)
        if self.sidetone:
            self.usb_paddle_handler.set_sidetone(self.sidetone)
            self.logger.info("Local sidetone enabled for paddle")
        else:
            self.logger.info("Using ExpertSDR3 sidetone only")
        
        return True
    
    def _configure_vail_adapter(self, vail_config: dict):
        """
        Configure Vail adapter firmware via MIDI
        
        Args:
            vail_config: Vail adapter configuration dictionary
        """
        self.logger.info("Configuring Vail adapter firmware via MIDI...")
        
        try:
            from vail_adapter_lib.midi_config import VailAdapterConfig
        except ImportError as e:
            self.logger.error(f"vail-adapter-lib not installed: {e}")
            self.logger.error("Install with: pip install -e ../vail-adapter-lib")
            self.logger.warning("Adapter will use stored EEPROM settings - speed may be incorrect!")
            return
        
        try:
            # Get configuration values
            keyer_mode = vail_config.get('keyer_mode', 8)
            speed_wpm = vail_config.get('speed_wpm', 25)
            sidetone_note = vail_config.get('sidetone_note', 73)
            output_mode = vail_config.get('output_mode', 'keyboard')
            keyboard_mode = (output_mode == 'keyboard')
            
            # Configure adapter
            configurator = VailAdapterConfig()
            success = configurator.configure_adapter(
                keyer_mode=keyer_mode,
                speed_wpm=speed_wpm,
                sidetone_note=sidetone_note,
                keyboard_mode=keyboard_mode
            )
            
            if success:
                keyer_names = {
                    0: "Passthrough", 1: "Straight", 2: "Bug", 3: "ElBug",
                    4: "SingleDot", 5: "Ultimatic", 6: "Plain",
                    7: "IambicA", 8: "IambicB", 9: "Keyahead"
                }
                self.logger.info(f"Vail adapter configured: {keyer_names.get(keyer_mode, 'Unknown')} at {speed_wpm} WPM")
            else:
                self.logger.warning("Failed to configure Vail adapter - check MIDI connection")
                self.logger.warning("Adapter may be using stored EEPROM settings")
                
        except Exception as e:
            self.logger.error(f"Error configuring Vail adapter: {e}", exc_info=True)
            self.logger.warning("Adapter will use stored EEPROM settings")
    
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
        # Track drive level changes
        if message.lower().startswith('drive:'):
            self.tci_client.parse_drive_from_message(message)
        
        # Track mode changes (for band change detection)
        if message.upper().startswith('MODULATION:'):
            self.tci_client.parse_modulation_from_message(message)
        
        # Log interesting messages
        if message.startswith(('VFO:', 'MODULATION:', 'TRX:', 'drive:')):
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
        Callback for each paddle key state change.
        
        Args:
            key_down: Current key state (True=pressed, False=released)
            previous_duration_ms: Duration of the previous state in milliseconds
                                  (used by TCI protocol for timing reconstruction)
        
        Note: TX is pre-armed in _on_paddle_tx_start before the first element,
              so KEYER commands here are sent with TX already active.
        Note: Sidetone is handled in usb_paddle_handler for minimal latency.
        """
        # Send to TCI
        if self.tci_client and self.tci_client.ready:
            # Cancel any pending PTT release when new keying starts
            if self.paddle_ptt_release_task and key_down:
                self.paddle_ptt_release_task.cancel()
                self.paddle_ptt_release_task = None
            
            # Send KEYER command (TX already armed by on_tx_start callback)
            await self.tci_client.send_keyer(key_down, previous_duration_ms)
            
            # Schedule PTT release after key-up with hangtime
            if not key_down and self.paddle_ptt_active:
                # Cancel any previous release task
                if self.paddle_ptt_release_task:
                    self.paddle_ptt_release_task.cancel()
                
                # Schedule new release after hangtime
                self.paddle_ptt_release_task = asyncio.create_task(
                    self._release_paddle_ptt_after_hangtime()
                )
    
    async def _on_paddle_tx_start(self):
        """
        Called BEFORE first iambic element to pre-arm TX.
        
        The TCI KEYER command requires TX to be active before it will generate
        a CW carrier. This callback is called when the paddle is first touched,
        BEFORE the iambic keyer generates any elements. This ensures TX is ready
        when the first dit/dah is sent.
        
        Sequence:
        1. Paddle touched → this callback fires
        2. MODULATION (if not CW) → TRX:true → wait tx_settle_time
        3. First KEYER command sent (TX already active)
        """
        if not self.tci_client or not self.tci_client.ready:
            return
        
        if not self.paddle_ptt_active:
            self.logger.debug("Arming TX for paddle keying")
            
            # Switch to CW mode if currently in a different mode (e.g., after band change)
            if self.tci_client.current_mode not in ('CW', 'CWL', 'CWU'):
                self.logger.debug(f"Mode is {self.tci_client.current_mode}, switching to CW")
                await self.tci_client.set_mode_cw()
            
            # Enable TX
            await self.tci_client.set_ptt(True)
            self.paddle_ptt_active = True
            
            # Wait for ExpertSDR3 to complete TX switch before first KEYER
            # Without this delay, the first dit/dah may be clipped or lost
            tx_settle_time = self.config['cw'].get('tx_settle_time', 0.050)
            await asyncio.sleep(tx_settle_time)
            
            self.logger.debug(f"TX pre-armed (mode={self.tci_client.current_mode}, settle={tx_settle_time*1000:.0f}ms)")
        else:
            self.logger.debug("TX already armed, skipping")
    
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
