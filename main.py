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
        
        # Vail firmware event buffering (50ms delay to allow TX settle)
        self.vail_event_buffer = []
        self.vail_buffer_task = None
        self.vail_tx_armed_time = None
    
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
            self.use_vail_firmware = True
        else:
            self.use_vail_firmware = False
        
        # Setup callbacks
        self.usb_paddle_handler.on_key_event = self._on_paddle_event
        self.usb_paddle_handler.on_tx_start = self._on_paddle_tx_start
        self.usb_paddle_handler.on_disconnect = self._on_paddle_disconnect
        
        # Pass sidetone to paddle handler for immediate audio feedback (if enabled)
        if self.sidetone:
            self.usb_paddle_handler.set_sidetone(self.sidetone)
            self.logger.info("Local sidetone enabled for paddle")
        else:
            self.logger.info("Using ExpertSDR3 sidetone only")
        
        return True
    
    def _configure_vail_adapter(self, vail_config: dict):
        """
        Configure Vail adapter firmware via MIDI with retry logic
        
        Args:
            vail_config: Vail adapter configuration dictionary
        """
        self.logger.info("Configuring Vail adapter firmware via MIDI...")
        
        # Get configuration values - use cw.keyer_mode as master setting
        keyer_mode_str = self.config['cw'].get('keyer_mode', 'iambic-b')
        
        # Map string keyer mode to Vail firmware mode number
        keyer_mode_map = {
            'straight': 1, 'iambic-a': 7, 'iambic-b': 8,
            'iambic_a': 7, 'iambic_b': 8  # Alternative format
        }
        keyer_mode = keyer_mode_map.get(keyer_mode_str.lower(), 8)  # Default to iambic-b
        
        speed_wpm = self.config['cw'].get('speed_wpm', 25)
        sidetone_note = vail_config.get('sidetone_note', 73)
        output_mode = vail_config.get('output_mode', 'keyboard')
        keyboard_mode = (output_mode == 'keyboard')
        
        # Retry logic for USB MIDI enumeration delay
        import time
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            if attempt > 0:
                self.logger.debug(f"Retrying MIDI configuration (attempt {attempt + 1}/{max_retries})...")
                time.sleep(retry_delay)
            
            success = self._configure_vail_adapter_safe(keyer_mode, speed_wpm, sidetone_note, keyboard_mode)
            
            if success:
                keyer_names = {
                    0: "Passthrough", 1: "Straight", 2: "Bug", 3: "ElBug",
                    4: "SingleDot", 5: "Ultimatic", 6: "Plain",
                    7: "IambicA", 8: "IambicB", 9: "Keyahead"
                }
                self.logger.info(f"Vail adapter configured: {keyer_names.get(keyer_mode, 'Unknown')} at {speed_wpm} WPM")
                return
        
        # All retries failed
        self.logger.warning("Failed to configure Vail adapter - check MIDI connection")
        self.logger.warning("Adapter may be using stored EEPROM settings")
    
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
    
    # ===== Helper Methods =====
    
    def _get_current_keyer_mode(self) -> int:
        """
        Get current keyer mode as MIDI mode number from master cw config
        
        Returns:
            MIDI keyer mode number (1-9)
        """
        keyer_mode_str = self.config['cw'].get('keyer_mode', 'iambic-b')
        
        # Map string keyer mode to Vail firmware mode number
        keyer_mode_map = {
            'straight': 1, 'iambic-a': 7, 'iambic-b': 8,
            'iambic_a': 7, 'iambic_b': 8  # Alternative format
        }
        return keyer_mode_map.get(keyer_mode_str.lower(), 8)  # Default to iambic-b
    
    def _configure_vail_adapter_safe(self, keyer_mode: int, speed_wpm: int, 
                                     sidetone_note: int, keyboard_mode: bool) -> bool:
        """
        Safely configure Vail adapter with error handling
        
        Args:
            keyer_mode: Keyer mode (0-9)
            speed_wpm: Speed in WPM
            sidetone_note: MIDI note for sidetone
            keyboard_mode: True for keyboard output, False for MIDI
            
        Returns:
            True if successful, False otherwise
        """
        try:
            from vail_adapter_lib.midi_config import VailAdapterConfig
            configurator = VailAdapterConfig()
            return configurator.configure_adapter(
                keyer_mode=keyer_mode,
                speed_wpm=speed_wpm,
                sidetone_note=sidetone_note,
                keyboard_mode=keyboard_mode
            )
        except ModuleNotFoundError:
            self.logger.debug("Vail adapter library not installed (pip install -e ../vail-adapter-lib)")
            return False
        except Exception as e:
            self.logger.error(f"Could not configure Vail adapter: {e}")
            return False
    
    async def _enable_paddle_ptt(self) -> bool:
        """
        Enable PTT for paddle keying (switches to CW mode if needed)
        
        Returns:
            True if PTT enabled successfully
        """
        if not self.paddle_ptt_active:
            # Switch to CW mode if needed
            if self.tci_client.current_mode not in ('CW', 'CWL', 'CWU'):
                await self.tci_client.set_mode_cw()
            
            # Enable PTT
            await self.tci_client.set_ptt(True)
            self.paddle_ptt_active = True
            self.logger.debug("TX enabled for paddle keying")
            return True
        return False  # Already active
    
    def _cleanup_usb_paddle(self):
        """Cleanup USB paddle handler (stop, disconnect, clear)"""
        if self.usb_paddle_handler:
            try:
                self.usb_paddle_handler.stop()
                self.usb_paddle_handler.disconnect()
            except Exception:
                pass
            self.usb_paddle_handler = None
    
    # ===== Callback Handlers =====
    
    def _on_tci_ready(self):
        """Callback when TCI server is ready"""
        self.logger.info("TCI server ready")
        
        # Pre-arm TX for Vail firmware to prevent first element clipping
        if hasattr(self, 'use_vail_firmware') and self.use_vail_firmware:
            asyncio.create_task(self._pre_arm_vail_tx())
        
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
    
    async def send_macro(self, message: str):
        """
        Send CW macro (for GUI button clicks)
        
        Args:
            message: CW message to send (with callsign already substituted)
        """
        await self._on_macro_send("GUI", message)
    
    async def update_cw_speed(self, wpm: int):
        """
        Update CW speed (for GUI slider)
        
        Args:
            wpm: Words per minute (15-40)
        """
        self.logger.info(f"Setting CW speed to {wpm} WPM")
        
        try:
            # Update TCI if connected
            if self.tci_client and self.tci_client.ready:
                await self.tci_client.set_cw_speed(wpm)
            else:
                self.logger.debug("TCI not ready - updating local config only")
            
            # Update config in memory (always)
            self.config['cw']['speed_wpm'] = wpm
            
            # Update USB paddle handler if active
            if self.usb_paddle_handler:
                self.usb_paddle_handler.set_wpm(wpm)
            
            # Update Vail adapter if enabled and dynamic speed allowed
            vail_config = self.config.get('vail_adapter', {})
            if vail_config.get('enabled', False) and vail_config.get('allow_dynamic_speed', False):
                self.logger.debug(f"Attempting to update Vail adapter speed to {wpm} WPM via MIDI...")
                
                # Get current keyer mode from master cw config (not cached vail_adapter value)
                keyer_mode = self._get_current_keyer_mode()
                sidetone_note = vail_config.get('sidetone_note', 73)
                keyboard_mode = (vail_config.get('output_mode', 'keyboard') == 'keyboard')
                
                success = self._configure_vail_adapter_safe(keyer_mode, wpm, sidetone_note, keyboard_mode)
                
                if success:
                    self.logger.info(f"✓ Vail adapter speed updated to {wpm} WPM via MIDI")
                    self.config['vail_adapter']['speed_wpm'] = wpm
                else:
                    self.logger.warning(f"✗ Vail adapter speed update failed")
                
        except Exception as e:
            self.logger.error(f"Error setting CW speed: {e}")
    
    async def update_sidetone(self, frequency: int, volume: float):
        """
        Update sidetone frequency and volume
        
        Args:
            frequency: Frequency in Hz (400-800)
            volume: Volume level (0.0-1.0)
        """
        self.logger.info(f"Setting sidetone to {frequency} Hz, {volume*100:.0f}%")
        
        # Update Python local sidetone
        if self.sidetone:
            self.sidetone.set_frequency(frequency)
            self.sidetone.set_volume(volume)
            self.logger.info(f"✓ Python sidetone updated: {frequency} Hz, {volume*100:.0f}%")
        else:
            self.logger.warning("Sidetone not initialized - skipping local sidetone update")
        
        # Update config
        self.config['sidetone']['frequency'] = frequency
        self.config['sidetone']['volume'] = volume
        
        # Update Vail adapter sidetone if enabled and dynamic updates allowed
        vail_config = self.config.get('vail_adapter', {})
        if vail_config.get('enabled', False) and vail_config.get('allow_dynamic_speed', False):
            # Convert frequency to MIDI note (A4 = 440Hz = note 69)
            import math
            midi_note = int(round(69 + 12 * math.log2(frequency / 440.0)))
            midi_note = max(0, min(127, midi_note))  # Clamp to MIDI range
            
            self.logger.info(f"Updating Vail adapter sidetone: {frequency} Hz = MIDI note {midi_note}")
            
            # Use helper method for consistency
            # Get current keyer mode from master cw config (not cached vail_adapter value)
            keyer_mode = self._get_current_keyer_mode()
            speed_wpm = self.config['cw'].get('speed_wpm', 25)
            keyboard_mode = (vail_config.get('output_mode', 'keyboard') == 'keyboard')
            
            success = self._configure_vail_adapter_safe(keyer_mode, speed_wpm, midi_note, keyboard_mode)
            
            if success:
                self.logger.info(f"✓ Vail adapter sidetone updated to {frequency} Hz (note {midi_note})")
                self.config['vail_adapter']['sidetone_note'] = midi_note
            else:
                self.logger.warning(f"✗ Vail adapter sidetone update failed")
    
    async def save_config_to_file(self, config: dict):
        """
        Save configuration to YAML file
        
        Args:
            config: Configuration dictionary to save
        """
        try:
            # Update internal config
            self.config = config.copy()
            
            # Write to file
            config_path = Path("config.yaml")
            with open(config_path, 'w') as f:
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
            
            self.logger.info(f"Config saved to {config_path}")
            
        except Exception as e:
            self.logger.error(f"Error saving config: {e}")
            raise
    
    async def reload_config(self) -> dict:
        """
        Reload configuration from YAML file
        
        Returns:
            Reloaded configuration dictionary
        """
        try:
            config_path = Path("config.yaml")
            with open(config_path, 'r') as f:
                new_config = yaml.safe_load(f)
            
            # Update internal config
            self.config = new_config
            
            self.logger.info(f"Config reloaded from {config_path}")
            return new_config
            
        except Exception as e:
            self.logger.error(f"Error reloading config: {e}")
            raise
    
    async def _on_paddle_event(self, key_down: bool, previous_duration_ms: int):
        """
        Callback for each paddle key state change.
        
        Args:
            key_down: Current key state (True=pressed, False=released)
            previous_duration_ms: Duration of the previous state in milliseconds
                                  (used by TCI protocol for timing reconstruction)
        
        Note: For iambic mode, TX is pre-armed in _on_paddle_tx_start before first element.
        Note: For Vail firmware mode, events are buffered 50ms to allow TX settle.
        Note: Sidetone is handled in usb_paddle_handler for minimal latency.
        """
        # Send to TCI
        if self.tci_client and self.tci_client.ready:
            # For Vail firmware: buffer events with 50ms delay for TX settle
            if hasattr(self, 'use_vail_firmware') and self.use_vail_firmware:
                # On first key-down, arm TX and start buffer processing
                if key_down and not self.paddle_ptt_active:
                    await self._enable_paddle_ptt()
                    self.vail_tx_armed_time = asyncio.get_event_loop().time()
                    self.logger.debug("Vail: TX armed, buffering events for 50ms settle")
                
                # Buffer the event with timestamp
                event_time = asyncio.get_event_loop().time()
                self.vail_event_buffer.append((key_down, previous_duration_ms, event_time))
                
                # Start buffer processing task if not running
                if self.vail_buffer_task is None or self.vail_buffer_task.done():
                    self.vail_buffer_task = asyncio.create_task(self._process_vail_buffer())
                
                # Cancel PTT release on new keying
                if self.paddle_ptt_release_task and key_down:
                    self.paddle_ptt_release_task.cancel()
                    self.paddle_ptt_release_task = None
                
                # Schedule PTT release after key-up
                if not key_down and self.paddle_ptt_active:
                    if self.paddle_ptt_release_task:
                        self.paddle_ptt_release_task.cancel()
                    self.paddle_ptt_release_task = asyncio.create_task(
                        self._release_paddle_ptt_after_hangtime()
                    )
                
            else:
                # For Python iambic: normal processing
                force_ptt = False
                settle_time_ms = 0
                
                if key_down and not self.paddle_ptt_active:
                    # Enable TX (switches to CW mode if needed)
                    force_ptt = self.config['tci'].get('force_ptt', False)
                    if not force_ptt:
                        await self._enable_paddle_ptt()
                    else:
                        # Settle time will be applied in send_keyer
                        await self._enable_paddle_ptt()
                        settle_time_ms = int(self.config['cw'].get('tx_settle_time', 0.050) * 1000)

                
                # Cancel any pending PTT release when new keying starts
                if self.paddle_ptt_release_task and key_down:
                    self.paddle_ptt_release_task.cancel()
                    self.paddle_ptt_release_task = None
                
                # Send KEYER command (settle delay only for Python iambic first element)
                await self.tci_client.send_keyer(key_down, previous_duration_ms, 
                                                force_ptt=force_ptt, 
                                                settle_time_ms=settle_time_ms)
                
                # Schedule PTT release after key-up with hangtime
                if not key_down and self.paddle_ptt_active:
                    if self.paddle_ptt_release_task:
                        self.paddle_ptt_release_task.cancel()
                    
                    self.paddle_ptt_release_task = asyncio.create_task(
                        self._release_paddle_ptt_after_hangtime()
                    )
    
    async def _process_vail_buffer(self):
        """Process buffered Vail firmware events - entire stream delayed 50ms"""
        try:
            tx_settle_time = self.config['cw'].get('tx_settle_time', 0.050)
            
            while self.vail_event_buffer:
                key_down, previous_duration_ms, event_time = self.vail_event_buffer[0]
                
                # Wait until 50ms after event arrived (constant delay for entire stream)
                target_send_time = event_time + tx_settle_time
                now = asyncio.get_event_loop().time()
                delay = max(0, target_send_time - now)
                
                if delay > 0:
                    await asyncio.sleep(delay)
                
                # Send the event with original timing preserved
                await self.tci_client.send_keyer(key_down, previous_duration_ms, 
                                                force_ptt=False, settle_time_ms=0)
                
                # Remove from buffer
                self.vail_event_buffer.pop(0)
                
        except Exception as e:
            self.logger.error(f"Error processing Vail buffer: {e}")
    
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
            
            # Enable TX (switches to CW mode if needed)
            await self._enable_paddle_ptt()
            
            # Wait for ExpertSDR3 to complete TX switch before first KEYER
            # Without this delay, the first dit/dah may be clipped or lost
            tx_settle_time = self.config['cw'].get('tx_settle_time', 0.050)
            await asyncio.sleep(tx_settle_time)
    
    async def _on_paddle_disconnect(self):
        """
        Called when USB paddle is disconnected.
        
        Ensures TX is released and notifies user.
        """
        self.logger.warning("USB paddle disconnected - manual keying disabled")
        
        # Release TX if paddle was active
        if self.paddle_ptt_active and self.tci_client and self.tci_client.ready:
            self.logger.debug("Releasing TX due to paddle disconnect")
            await self.tci_client.set_ptt(False)
            self.paddle_ptt_active = False
        
        # Stop sidetone if active
        if self.sidetone:
            self.sidetone.set_key(False)
        
        # Clean up handler (stop and disconnect)
        self._cleanup_usb_paddle()
        
        # Start auto-reconnection monitoring if enabled
        if self.config.get('usb_hid', {}).get('auto_reconnect', True):
            self.logger.info("Starting USB paddle auto-reconnection monitoring...")
            asyncio.create_task(self._monitor_usb_reconnection())
    
    async def _monitor_usb_reconnection(self):
        """
        Monitor for USB paddle reconnection after disconnect.
        
        Polls every 2 seconds for up to 5 minutes.
        """
        max_attempts = 150  # 5 minutes (150 * 2s)
        attempt = 0
        
        while attempt < max_attempts and self.running and self.usb_paddle_handler is None:
            await asyncio.sleep(2.0)
            attempt += 1
            
            # Try to reconnect
            success = await self.reconnect_usb_paddle(silent=True)
            if success:
                self.logger.info("✓ USB paddle auto-reconnected successfully!")
                return
        
        if attempt >= max_attempts:
            self.logger.info("USB paddle auto-reconnection monitoring stopped (timeout)")
    
    async def reconnect_usb_paddle(self, silent: bool = False) -> bool:
        """
        Attempt to reconnect USB paddle.
        
        Args:
            silent: If True, suppress log messages for failed attempts
            
        Returns:
            bool: True if reconnected successfully, False otherwise
        """
        # If handler exists, check if it's still actually connected
        if self.usb_paddle_handler is not None:
            # Check if device is still responsive AND poll loop is still running
            poll_running = getattr(self.usb_paddle_handler, 'running', False)
            
            if hasattr(self.usb_paddle_handler.hid_reader, 'is_connected'):
                is_connected = self.usb_paddle_handler.hid_reader.is_connected()
                
                # If both connected AND polling, device is truly active
                if is_connected and poll_running:
                    if not silent:
                        self.logger.warning("USB paddle already connected and active")
                    return True
                else:
                    # Device disconnected OR poll loop stopped - clean it up
                    if not silent:
                        status = "disconnected" if not is_connected else "poll loop stopped"
                        self.logger.info(f"Cleaning up stale USB paddle connection ({status})...")
                    self._cleanup_usb_paddle()
            else:
                # No validation method - just check if poll loop is running
                if poll_running:
                    if not silent:
                        self.logger.warning("USB paddle already connected (no device validation)")
                    return True
                else:
                    if not silent:
                        self.logger.info("Poll loop not running - cleaning up...")
                    self._cleanup_usb_paddle()
        
        if not silent:
            self.logger.info("Attempting to reconnect USB paddle...")
        
        # Try to initialize paddle handler (this creates new handler and connects)
        success = await asyncio.to_thread(self._initialize_usb_paddle)
        
        if success and self.usb_paddle_handler:
            if not silent:
                self.logger.info("✓ USB paddle reconnected successfully")
            
            # Reconfigure Vail adapter firmware if enabled
            # (device reset on reconnection loses configuration)
            vail_config = self.config.get('vail_adapter', {})
            if vail_config.get('enabled', False):
                if not silent:
                    self.logger.info("Reconfiguring Vail adapter after reconnection...")
                # Run MIDI config in thread to avoid blocking
                await asyncio.to_thread(self._configure_vail_adapter, vail_config)
            
            # Start polling task
            asyncio.create_task(self.usb_paddle_handler.poll_loop())
            return True
        else:
            if not silent:
                self.logger.debug("USB paddle not found (still disconnected)")
            return False
    
    async def _pre_arm_vail_tx(self):
        """Pre-arm TX for Vail firmware at startup to prevent first element clipping"""
        try:
            await asyncio.sleep(0.5)  # Wait for TCI to stabilize
            
            if self.tci_client and self.tci_client.ready:
                # Set CW mode
                if self.tci_client.current_mode not in ('CW', 'CWL', 'CWU'):
                    await self.tci_client.set_mode_cw()
                
                # Enable TX and wait for settle
                await self.tci_client.set_ptt(True)
                tx_settle_time = self.config['cw'].get('tx_settle_time', 0.050)
                await asyncio.sleep(tx_settle_time)
                
                self.paddle_ptt_active = True
                self.logger.info(f"Vail firmware: TX pre-armed at startup (settle={tx_settle_time*1000:.0f}ms)")
        except Exception as e:
            self.logger.error(f"Failed to pre-arm TX for Vail firmware: {e}")
    
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
        
        # Stop USB paddle (stop, disconnect, clear)
        self._cleanup_usb_paddle()
        
        # Close sidetone
        if self.sidetone:
            self.sidetone.close()
        
        # Disconnect TCI
        if self.tci_client:
            await self.tci_client.disconnect()
        
        self.logger.info("Shutdown complete")


async def main():
    """Application entry point"""
    import argparse
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="TCI CW Controller")
    parser.add_argument('--gui', action='store_true', help="Launch with GUI")
    parser.add_argument('--config', default='config.yaml', help="Config file path")
    args = parser.parse_args()
    
    # Run controller
    controller = TCICWController(config_path=args.config)
    
    if args.gui:
        # Launch with GUI - run asyncio in background thread
        from gui_window import TkinterGUI
        import threading
        
        # Create a new event loop for the background thread
        loop = asyncio.new_event_loop()
        
        # Start controller in background thread with its own event loop
        def run_controller():
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(controller.run())
            except Exception as e:
                logging.error(f"Controller thread error: {e}")
        
        controller_thread = threading.Thread(target=run_controller, daemon=True)
        controller_thread.start()
        
        # Wait for controller to initialize (TCI connection, sidetone, etc.)
        import time
        max_wait = 10  # seconds
        wait_step = 0.1
        waited = 0
        
        while waited < max_wait:
            if controller.tci_client and controller.tci_client.ready:
                logging.info("Controller initialized and ready")
                break
            time.sleep(wait_step)
            waited += wait_step
        
        if waited >= max_wait:
            logging.warning("Controller initialization timeout - GUI may show incorrect status")
        
        # Create and run GUI in main thread (blocks until window closed)
        gui = TkinterGUI(controller, loop)
        
        # Handle window close
        def on_closing():
            from tkinter import messagebox
            if messagebox.askokcancel("Quit", "Quit TCI CW Controller?"):
                # Shutdown controller in background thread
                future = asyncio.run_coroutine_threadsafe(controller.shutdown(), loop)
                try:
                    future.result(timeout=2.0)
                except:
                    pass
                gui.root.destroy()
        
        gui.root.protocol("WM_DELETE_WINDOW", on_closing)
        gui.run()
        
        # GUI closed, ensure controller is shutdown
        if controller.running:
            try:
                future = asyncio.run_coroutine_threadsafe(controller.shutdown(), loop)
                future.result(timeout=2.0)
            except:
                pass
            controller_thread.join(timeout=2.0)
        
        exit_code = 0
    else:
        # Run in CLI mode
        # Handle Ctrl+C gracefully
        loop = asyncio.get_event_loop()
        
        def signal_handler(sig, frame):
            """Handle interrupt signal"""
            print("\n\nInterrupt received, shutting down...")
            asyncio.create_task(controller.shutdown())
            # Cancel all tasks
            for task in asyncio.all_tasks(loop):
                task.cancel()
        
        signal.signal(signal.SIGINT, signal_handler)
        
        exit_code = await controller.run()
    
    sys.exit(exit_code)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
