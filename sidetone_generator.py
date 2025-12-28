#!/usr/bin/env python3
"""
Sidetone Generator

Generates local audio sidetone for CW keying using PyAudio.
Provides instant audio feedback for manual paddle operation.
"""

import numpy as np
import pyaudio
import threading
import queue


class SidetoneGenerator:
    """Real-time sidetone generator using PyAudio"""
    
    def __init__(self, frequency: int = 600, sample_rate: int = 44100, 
                 volume: float = 0.5, buffer_size: int = 1024):
        """
        Initialize sidetone generator
        
        Args:
            frequency: Tone frequency in Hz (typical: 400-800)
            sample_rate: Audio sample rate
            volume: Volume level (0.0-1.0)
            buffer_size: Audio buffer size (smaller = lower latency)
        """
        self.frequency = frequency
        self.sample_rate = sample_rate
        self.volume = volume
        self.buffer_size = buffer_size
        
        # State
        self.key_down = False
        self.running = False
        self.phase = 0.0
        
        # PyAudio setup
        self.p = pyaudio.PyAudio()
        self.stream = None
        
        # Thread-safe command queue
        self.command_queue = queue.Queue()
        
        # Start audio stream
        self._start_stream()
    
    def _start_stream(self):
        """Start PyAudio output stream"""
        try:
            self.stream = self.p.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=self.buffer_size,
                stream_callback=self._audio_callback
            )
            self.running = True
            self.stream.start_stream()
            
        except Exception as e:
            raise RuntimeError(f"Failed to initialize audio: {e}")
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """
        PyAudio callback - generates audio samples
        
        Called by audio thread, must be thread-safe and fast
        """
        # Process any pending commands from main thread
        while not self.command_queue.empty():
            try:
                cmd, value = self.command_queue.get_nowait()
                if cmd == 'key':
                    self.key_down = value
                elif cmd == 'frequency':
                    self.frequency = value
                    self.phase = 0.0  # Reset phase on frequency change
            except queue.Empty:
                break
        
        # Generate audio samples
        if self.key_down:
            # Generate sine wave
            samples = np.arange(frame_count, dtype=np.float32)
            omega = 2.0 * np.pi * self.frequency / self.sample_rate
            audio = np.sin(omega * (samples + self.phase)) * self.volume
            
            # Update phase for continuity
            self.phase += frame_count
            self.phase = self.phase % (self.sample_rate / self.frequency)
        else:
            # Silence
            audio = np.zeros(frame_count, dtype=np.float32)
            self.phase = 0.0  # Reset phase when key up
        
        return (audio.tobytes(), pyaudio.paContinue)
    
    def set_key(self, key_down: bool):
        """
        Set key state (on/off)
        
        Args:
            key_down: True for key down (tone on), False for key up (tone off)
        """
        self.command_queue.put(('key', key_down))
    
    def set_frequency(self, frequency: int):
        """
        Change sidetone frequency
        
        Args:
            frequency: New frequency in Hz
        """
        self.command_queue.put(('frequency', frequency))
    
    def close(self):
        """Stop and close audio stream"""
        self.running = False
        
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        
        if self.p:
            try:
                self.p.terminate()
            except Exception:
                pass
            self.p = None
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
