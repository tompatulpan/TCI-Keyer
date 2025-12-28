#!/usr/bin/env python3
"""
Keyboard Handler for F-Key CW Macros

Captures F1-F12 key presses and sends configured CW messages via TCI.
"""

import asyncio
import logging
from pynput import keyboard
from typing import Dict, Optional, Callable


class KeyboardHandler:
    """Handle F1-F12 keyboard input for CW macros"""
    
    def __init__(self, function_keys: Dict[str, str], callsign: str, event_loop=None):
        """
        Initialize keyboard handler
        
        Args:
            function_keys: Dict mapping F-key names to CW message templates
            callsign: Operator callsign for {callsign} substitution
            event_loop: Main event loop for scheduling async callbacks
        """
        self.function_keys = function_keys
        self.callsign = callsign
        self.listener: Optional[keyboard.Listener] = None
        self.running = False
        self.event_loop = event_loop
        
        # Callback when F-key pressed
        self.on_macro_send: Optional[Callable[[str, str], None]] = None
        
        self.logger = logging.getLogger("KeyboardHandler")
    
    def _substitute_message(self, template: str) -> str:
        """
        Substitute placeholders in message template
        
        Args:
            template: Message template with {callsign} placeholder
            
        Returns:
            Message with substitutions applied
        """
        return template.replace('{callsign}', self.callsign)
    
    def _on_press(self, key):
        """
        Handle key press event
        
        Args:
            key: Key object from pynput
        """
        try:
            # Check if it's a function key
            if hasattr(key, 'name') and key.name.startswith('f'):
                key_name = key.name.upper()  # f1 → F1
                
                # Check if this F-key is configured
                if key_name in self.function_keys:
                    message_template = self.function_keys[key_name]
                    
                    # Skip if empty
                    if not message_template or message_template.strip() == "":
                        self.logger.debug(f"{key_name} not assigned")
                        return
                    
                    # Substitute callsign
                    message = self._substitute_message(message_template)
                    
                    self.logger.info(f"{key_name}: {message}")
                    
                    # Call callback (thread-safe async scheduling)
                    if self.on_macro_send and self.event_loop:
                        # Schedule callback in main event loop from this thread
                        asyncio.run_coroutine_threadsafe(
                            self.on_macro_send(key_name, message),
                            self.event_loop
                        )
                        
        except Exception as e:
            self.logger.error(f"Error handling key press: {e}")
    
    def start(self):
        """Start keyboard listener"""
        if self.running:
            return
        
        self.logger.info("Starting F-key listener (F1-F12 for CW macros)")
        self.running = True
        
        # Start pynput listener in background thread
        self.listener = keyboard.Listener(on_press=self._on_press)
        self.listener.start()
    
    def stop(self):
        """Stop keyboard listener"""
        if not self.running:
            return
        
        self.running = False
        
        if self.listener:
            self.listener.stop()
            self.listener = None
        
        self.logger.info("Stopped F-key listener")
    
    def __enter__(self):
        """Context manager entry"""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.stop()
