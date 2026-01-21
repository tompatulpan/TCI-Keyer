"""
Vail Adapter MIDI Configuration Module

Configures the Vail adapter firmware via MIDI control messages.
The adapter accepts MIDI CC and PC messages to set:
- Keyer mode (Program Change 0-9)
- Speed/dit duration (CC1)
- Sidetone frequency (CC2)
- Output mode (CC0: keyboard vs MIDI)
"""

import logging
from typing import Optional

try:
    import mido
    MIDO_AVAILABLE = True
except ImportError:
    MIDO_AVAILABLE = False

logger = logging.getLogger(__name__)


class VailAdapterConfig:
    """Configure Vail adapter via MIDI messages."""
    
    # Keyer mode constants
    KEYER_PASSTHROUGH = 0
    KEYER_STRAIGHT = 1
    KEYER_BUG = 2
    KEYER_ELBUG = 3
    KEYER_SINGLEDOT = 4
    KEYER_ULTIMATIC = 5
    KEYER_PLAIN_IAMBIC = 6
    KEYER_IAMBIC_A = 7
    KEYER_IAMBIC_B = 8
    KEYER_KEYAHEAD = 9
    
    KEYER_NAMES = {
        0: "Passthrough",
        1: "Straight Key",
        2: "Bug",
        3: "ElBug",
        4: "Single Dot",
        5: "Ultimatic",
        6: "Plain Iambic",
        7: "Iambic A",
        8: "Iambic B",
        9: "Keyahead"
    }
    
    def __init__(self, device_name: str = "Vail Adapter"):
        """
        Initialize Vail adapter configuration.
        
        Args:
            device_name: MIDI device name to search for
        """
        self.device_name = device_name
        self.port: Optional[mido.ports.BaseOutput] = None
        
    def find_vail_adapter(self) -> bool:
        """
        Search for Vail adapter MIDI device.
        
        Returns:
            True if device found, False otherwise
        """
        if not MIDO_AVAILABLE:
            logger.error("mido library not installed. Install with: pip install mido python-rtmidi")
            return False
            
        try:
            output_names = mido.get_output_names()
            logger.debug(f"Available MIDI outputs: {output_names}")
            
            # Look for Vail Adapter or XIAO device names
            search_patterns = ["Vail Adapter", "vail", "XIAO", "xiao", "Seeed"]
            for name in output_names:
                for pattern in search_patterns:
                    if pattern in name:
                        self.device_name = name
                        logger.info(f"Found MIDI device: {name}")
                        return True
            
            logger.warning(f"No compatible MIDI device found. Available: {output_names}")
            return False
            
        except Exception as e:
            logger.error(f"Error searching for MIDI devices: {e}")
            return False
    
    def open(self) -> bool:
        """
        Open MIDI connection to Vail adapter.
        
        Returns:
            True if connection successful, False otherwise
        """
        if not MIDO_AVAILABLE:
            logger.error("mido library not available")
            return False
            
        try:
            if not self.find_vail_adapter():
                return False
                
            self.port = mido.open_output(self.device_name)
            logger.info(f"Opened MIDI connection to {self.device_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to open MIDI port: {e}")
            return False
    
    def close(self):
        """Close MIDI connection."""
        if self.port:
            try:
                self.port.close()
                logger.debug("MIDI port closed")
            except Exception as e:
                logger.error(f"Error closing MIDI port: {e}")
            finally:
                self.port = None
    
    def set_keyer_mode(self, mode: int) -> bool:
        """
        Set keyer mode via MIDI Program Change.
        
        Args:
            mode: Keyer mode 0-9
            
        Returns:
            True if command sent successfully
        """
        if not self.port:
            logger.error("MIDI port not open")
            return False
            
        if mode < 0 or mode > 9:
            logger.error(f"Invalid keyer mode: {mode} (must be 0-9)")
            return False
            
        try:
            msg = mido.Message('program_change', program=mode)
            self.port.send(msg)
            logger.info(f"Set keyer mode to {mode} ({self.KEYER_NAMES.get(mode, 'Unknown')})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send keyer mode: {e}")
            return False
    
    def set_speed(self, wpm: int) -> bool:
        """
        Set keyer speed via MIDI CC1.
        
        Args:
            wpm: Speed in words per minute
            
        Returns:
            True if command sent successfully
        """
        if not self.port:
            logger.error("MIDI port not open")
            return False
            
        # Calculate dit duration in milliseconds
        dit_duration_ms = 1200 / wpm
        
        # CC1 value is dit_duration / 2
        cc1_value = int(dit_duration_ms / 2)
        
        # Clamp to valid MIDI range (0-127)
        cc1_value = max(0, min(127, cc1_value))
        
        try:
            msg = mido.Message('control_change', control=1, value=cc1_value)
            self.port.send(msg)
            logger.info(f"Set speed to {wpm} WPM (dit={dit_duration_ms:.1f}ms, CC1={cc1_value})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send speed: {e}")
            return False
    
    def set_sidetone_note(self, midi_note: int) -> bool:
        """
        Set sidetone frequency via MIDI CC2.
        
        Args:
            midi_note: MIDI note number (0-127)
                      Common values: 60=C4 (261Hz), 69=A4 (440Hz), 72=C5 (523Hz)
            
        Returns:
            True if command sent successfully
        """
        if not self.port:
            logger.error("MIDI port not open")
            return False
            
        if midi_note < 0 or midi_note > 127:
            logger.error(f"Invalid MIDI note: {midi_note} (must be 0-127)")
            return False
            
        try:
            msg = mido.Message('control_change', control=2, value=midi_note)
            self.port.send(msg)
            logger.info(f"Set sidetone to MIDI note {midi_note}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send sidetone note: {e}")
            return False
    
    def set_output_mode(self, keyboard_mode: bool = True) -> bool:
        """
        Set output mode via MIDI CC0.
        
        Args:
            keyboard_mode: True for keyboard mode (Ctrl keys), False for MIDI mode
            
        Returns:
            True if command sent successfully
        """
        if not self.port:
            logger.error("MIDI port not open")
            return False
            
        # CC0: 64-127 = keyboard mode, 0-63 = MIDI mode
        cc0_value = 127 if keyboard_mode else 0
        
        try:
            msg = mido.Message('control_change', control=0, value=cc0_value)
            self.port.send(msg)
            mode_str = "keyboard" if keyboard_mode else "MIDI"
            logger.info(f"Set output mode to {mode_str}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send output mode: {e}")
            return False
    
    def configure_adapter(self, keyer_mode: int, speed_wpm: int, 
                         sidetone_note: int = 69, keyboard_mode: bool = True) -> bool:
        """
        Complete configuration of Vail adapter.
        
        Args:
            keyer_mode: Keyer type (0-9)
            speed_wpm: Speed in WPM
            sidetone_note: MIDI note for sidetone (default 69 = 440Hz)
            keyboard_mode: True for keyboard output, False for MIDI
            
        Returns:
            True if all commands sent successfully
        """
        if not self.open():
            return False
        
        try:
            success = True
            success &= self.set_output_mode(keyboard_mode)
            success &= self.set_keyer_mode(keyer_mode)
            success &= self.set_speed(speed_wpm)
            success &= self.set_sidetone_note(sidetone_note)
            
            if success:
                logger.info("Vail adapter configured successfully")
            else:
                logger.warning("Some Vail adapter configuration commands failed")
                
            return success
            
        finally:
            self.close()


def frequency_to_midi_note(frequency_hz: float) -> int:
    """
    Convert frequency in Hz to nearest MIDI note number.
    
    Args:
        frequency_hz: Frequency in Hertz
        
    Returns:
        MIDI note number (0-127)
    """
    import math
    
    # MIDI note formula: note = 12 * log2(freq / 440) + 69
    note = 12 * math.log2(frequency_hz / 440.0) + 69
    return max(0, min(127, int(round(note))))


def midi_note_to_frequency(midi_note: int) -> float:
    """
    Convert MIDI note number to frequency in Hz.
    
    Args:
        midi_note: MIDI note number (0-127)
        
    Returns:
        Frequency in Hertz
    """
    # Frequency formula: freq = 440 * 2^((note - 69) / 12)
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


# Quick test/usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    config = VailAdapterConfig()
    
    # Example: Configure for Iambic B at 25 WPM with 600 Hz sidetone
    sidetone_midi = frequency_to_midi_note(600)
    print(f"600 Hz = MIDI note {sidetone_midi} ({midi_note_to_frequency(sidetone_midi):.1f} Hz)")
    
    success = config.configure_adapter(
        keyer_mode=VailAdapterConfig.KEYER_IAMBIC_B,
        speed_wpm=25,
        sidetone_note=sidetone_midi,
        keyboard_mode=True
    )
    
    if success:
        print("Configuration successful!")
    else:
        print("Configuration failed - is Vail adapter connected?")
