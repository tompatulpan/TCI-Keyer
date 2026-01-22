#!/usr/bin/env python3
"""
Tkinter GUI for TCI CW Controller
Provides status display, F-key macro editor, and live settings adjustment
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import asyncio
import threading
import logging
from version import get_version_string

logger = logging.getLogger(__name__)


class TkinterGUI:
    """
    Main GUI window for TCI CW Controller
    
    Features:
    - Status indicators (TCI/USB connection)
    - F-key macro editor (F1-F12 buttons + text fields)
    - Live settings (CW speed, sidetone freq/volume)
    - Asyncio integration via root.after() polling
    """
    
    def __init__(self, controller, loop):
        """
        Initialize GUI window
        
        Args:
            controller: TCICWController instance (for state access and control)
            loop: asyncio event loop (for run_coroutine_threadsafe)
        """
        self.controller = controller
        self.loop = loop
        self.root = tk.Tk()
        self.root.title(f"TCI CW Controller {get_version_string()}")
        self.root.geometry("570x660")
        
        # Configuration cache (edited in memory, saved on button)
        self.config = controller.config.copy()
        
        # Status variables
        self.tci_status_var = tk.StringVar(value="Disconnected")
        self.usb_status_var = tk.StringVar(value="Disconnected")
        self.usb_device_var = tk.StringVar(value="Unknown")
        self.active_macro_indicator = None  # Will be set in _create_status_frame
        
        # Macro text widgets (for live editing)
        self.macro_entries = {}  # F1-F12 -> Text widget
        
        # Setting variables (will be updated after controller initializes)
        self.callsign_var = tk.StringVar(value="")
        self.keyer_mode_var = tk.StringVar(value="iambic-b")
        self.cw_speed_var = tk.IntVar(value=25)
        self.sidetone_freq_var = tk.IntVar(value=600)
        self.sidetone_vol_var = tk.DoubleVar(value=50.0)
        
        # Throttling for slider updates (prevent flooding)
        self._speed_update_pending = None
        self._sidetone_update_pending = None
        
        # Build UI
        self._create_widgets()
        
        # Load actual values from controller after short delay (let it initialize)
        self.root.after(1000, self._load_initial_values)
        
        # Start asyncio polling
        self._schedule_asyncio_step()
        self._schedule_status_update()
        
    def _create_widgets(self):
        """Build all GUI components"""
        # Main container with scrollbar
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Sections: Operator Info, Status, Macros, Settings
        self._create_operator_frame(main_frame)
        self._create_status_frame(main_frame)
        self._create_macros_frame(main_frame)
        self._create_settings_frame(main_frame)
        
        # Bottom buttons
        self._create_bottom_buttons(main_frame)
    
    def _create_operator_frame(self, parent):
        """Operator callsign and keyer mode selection"""
        frame = ttk.LabelFrame(parent, text="Operator", padding="10")
        frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5)
        
        # Callsign
        ttk.Label(frame, text="Callsign:").grid(row=0, column=0, sticky=tk.W, padx=5)
        callsign_entry = ttk.Entry(frame, textvariable=self.callsign_var, width=12)
        callsign_entry.grid(row=0, column=1, sticky=tk.W, padx=5)
        self.callsign_var.trace_add('write', lambda *args: self._on_callsign_change())
        
        # Keyer Mode
        ttk.Label(frame, text="Keyer Mode:").grid(row=0, column=2, sticky=tk.W, padx=(20, 5))
        keyer_combo = ttk.Combobox(frame, textvariable=self.keyer_mode_var, width=12,
                                   values=["straight", "iambic-a", "iambic-b"],
                                   state="readonly")
        keyer_combo.grid(row=0, column=3, sticky=tk.W, padx=5)
        keyer_combo.bind('<<ComboboxSelected>>', self._on_keyer_mode_change)
        
    def _create_status_frame(self, parent):
        """Compact status indicators"""
        frame = ttk.LabelFrame(parent, text="Status", padding="5")
        frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)
        
        # Status row
        ttk.Label(frame, text="TCI:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.tci_indicator = tk.Label(frame, textvariable=self.tci_status_var, 
                                      bg="red", fg="white", width=12, relief=tk.SUNKEN)
        self.tci_indicator.grid(row=0, column=1, padx=5)
        
        ttk.Label(frame, text="USB:").grid(row=0, column=2, sticky=tk.W, padx=(15, 5))
        self.usb_indicator = tk.Label(frame, textvariable=self.usb_status_var,
                                      bg="red", fg="white", width=12, relief=tk.SUNKEN)
        self.usb_indicator.grid(row=0, column=3, padx=5)
        
        ttk.Label(frame, text="Device:").grid(row=0, column=4, sticky=tk.W, padx=(15, 5))
        ttk.Label(frame, textvariable=self.usb_device_var,
                 font=('TkDefaultFont', 9)).grid(row=0, column=5, sticky=tk.W, padx=5)
        
        # Active macro LED indicator (no label, just LED)
        self.active_macro_indicator = tk.Label(frame, text="●", fg="gray", 
                                               font=('Arial', 16), width=1)
        self.active_macro_indicator.grid(row=0, column=6, sticky=tk.W, padx=(15, 5))
        
    def _create_macros_frame(self, parent):
        """Compact F-key macro buttons"""
        frame = ttk.LabelFrame(parent, text="F-Key Macros (use {callsign} for substitution)", padding="10")
        frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        parent.rowconfigure(2, weight=1)
        
        # Create 12 compact macro editors (2 columns x 6 rows)
        for i in range(1, 13):
            row = (i - 1) // 2
            col = (i - 1) % 2
            
            fkey = f"F{i}"
            self._create_compact_macro_editor(frame, fkey, row, col)
            
    def _create_compact_macro_editor(self, parent, fkey, row, col):
        """Create compact F-key editor widget (button + entry only)"""
        container = ttk.Frame(parent)
        container.grid(row=row, column=col, padx=5, pady=5, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Button to send macro
        btn = ttk.Button(container, text=fkey, width=5,
                        command=lambda: self._send_macro(fkey))
        btn.grid(row=0, column=0, sticky=tk.W)
        
        # Text entry (single line, compact)
        text_widget = tk.Entry(container, width=18)
        text_widget.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(5, 0))
        
        # Load current macro text
        current_text = self.config['function_keys'].get(fkey, "")
        text_widget.insert(0, current_text)
        
        # Store reference
        self.macro_entries[fkey] = text_widget
        
        # Make entry expandable
        container.columnconfigure(1, weight=1)
        
    def _create_settings_frame(self, parent):
        """Live settings adjustment (sliders)"""
        frame = ttk.LabelFrame(parent, text="Settings", padding="10")
        frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=5)
        
        # CW Speed
        ttk.Label(frame, text="CW Speed (WPM):").grid(row=0, column=0, sticky=tk.W, padx=5)
        speed_scale = ttk.Scale(frame, from_=15, to=40, orient=tk.HORIZONTAL,
                               variable=self.cw_speed_var, command=lambda v: self._on_speed_change(v))
        speed_scale.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=5)
        self.speed_label = ttk.Label(frame, text=f"{self.cw_speed_var.get()} WPM", width=8)
        self.speed_label.grid(row=0, column=2, padx=5)
        
        # Sidetone Frequency
        ttk.Label(frame, text="Sidetone Freq (Hz):").grid(row=1, column=0, sticky=tk.W, padx=5)
        freq_scale = ttk.Scale(frame, from_=400, to=800, orient=tk.HORIZONTAL,
                              variable=self.sidetone_freq_var, command=lambda v: self._on_freq_change(v))
        freq_scale.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=5)
        self.freq_label = ttk.Label(frame, text=f"{self.sidetone_freq_var.get()} Hz", width=8)
        self.freq_label.grid(row=1, column=2, padx=5)
        
        # Sidetone Volume
        ttk.Label(frame, text="Sidetone Volume (%):").grid(row=2, column=0, sticky=tk.W, padx=5)
        vol_scale = ttk.Scale(frame, from_=0, to=100, orient=tk.HORIZONTAL,
                             variable=self.sidetone_vol_var, command=lambda v: self._on_volume_change(v))
        vol_scale.grid(row=2, column=1, sticky=(tk.W, tk.E), padx=5)
        self.vol_label = ttk.Label(frame, text=f"{self.sidetone_vol_var.get():.0f}%", width=8)
        self.vol_label.grid(row=2, column=2, padx=5)
        
        # Make sliders expandable
        frame.columnconfigure(1, weight=1)
        
    def _create_bottom_buttons(self, parent):
        """Save/Load/Quit buttons"""
        button_frame = ttk.Frame(parent)
        button_frame.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=10)
        
        ttk.Button(button_frame, text="Save Config", 
                  command=self._save_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Reload Config", 
                  command=self._reload_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Reconnect USB Paddle", 
                  command=self._reconnect_usb_paddle).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Quit", 
                  command=self._quit).pack(side=tk.RIGHT, padx=5)
        
    # Callbacks
    
    def _load_initial_values(self):
        """Load initial values from controller after initialization"""
        try:
            # Reload config from controller
            self.config = self.controller.config.copy()
            
            # Update callsign and keyer mode
            self.callsign_var.set(self.config['operator']['callsign'])
            self.keyer_mode_var.set(self.config['cw']['keyer_mode'])
            
            # Update slider values
            self.cw_speed_var.set(self.config['cw']['speed_wpm'])
            self.sidetone_freq_var.set(self.config['sidetone']['frequency'])
            self.sidetone_vol_var.set(self.config['sidetone']['volume'] * 100)
            
            # Update labels
            self.speed_label.config(text=f"{self.cw_speed_var.get()} WPM")
            self.freq_label.config(text=f"{self.sidetone_freq_var.get()} Hz")
            self.vol_label.config(text=f"{self.sidetone_vol_var.get():.0f}%")
            
            logger.info(f"[GUI] Loaded initial values: {self.callsign_var.get()}, "
                       f"{self.keyer_mode_var.get()}, {self.cw_speed_var.get()} WPM")
        except Exception as e:
            logger.error(f"[GUI] Error loading initial values: {e}")
    
    def _on_callsign_change(self):
        """Callsign entry changed"""
        new_callsign = self.callsign_var.get().upper()
        self.config['operator']['callsign'] = new_callsign
        logger.debug(f"[GUI] Callsign changed to: {new_callsign}")
    
    def _reconnect_usb_paddle(self):
        """Reconnect USB paddle (manual trigger)"""
        logger.info("[GUI] USB paddle reconnection requested by user")
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.controller.reconnect_usb_paddle(),
                self.loop
            )
            # Show result after short delay
            def check_result():
                try:
                    result = future.result(timeout=0.1)
                    if result:
                        messagebox.showinfo("USB Paddle", "USB paddle reconnected successfully!")
                    else:
                        messagebox.showwarning("USB Paddle", "USB paddle not found. Make sure device is connected.")
                except Exception as e:
                    messagebox.showerror("USB Paddle", f"Reconnection failed: {e}")
            
            self.root.after(1500, check_result)  # Give it time to connect
        except Exception as e:
            logger.error(f"[GUI] Error requesting USB paddle reconnection: {e}")
            messagebox.showerror("USB Paddle", f"Reconnection failed: {e}")
    
    def _on_keyer_mode_change(self, event=None):
        """Keyer mode combo changed - saves to config, requires restart"""
        new_mode = self.keyer_mode_var.get()
        old_mode = self.config['cw']['keyer_mode']
        
        if new_mode != old_mode:
            self.config['cw']['keyer_mode'] = new_mode
            logger.info(f"[GUI] Keyer mode changed to: {new_mode} (save config and restart to apply)")
            
            # Show info message
            messagebox.showinfo("Keyer Mode Changed", 
                              f"Keyer mode set to {new_mode}\n\n"
                              "Save config and restart application to apply.")
    
    def _update_preview(self, fkey):
        """Update preview label with callsign substitution (removed - for compact GUI)"""
        pass
            
    def _send_macro(self, fkey):
        """Send F-key macro via controller"""
        # Get current text from entry widget
        text = self.macro_entries[fkey].get().strip()
        
        if not text:
            messagebox.showwarning("Empty Macro", f"{fkey} has no text configured")
            return
            
        # Substitute callsign
        callsign = self.config['operator']['callsign']
        message = text.replace('{callsign}', callsign)
        
        # Update active macro indicator (LED to bright green)
        self.active_macro_indicator.config(fg="#00ff00")
        
        # Send via controller (async)
        future = asyncio.run_coroutine_threadsafe(
            self.controller.send_macro(message),
            self.loop
        )
        
        # Clear indicator after send (LED back to gray)
        self.root.after(500, lambda: self.active_macro_indicator.config(fg="gray"))
        
        logger.info(f"[GUI] {fkey} clicked: {message}")
        
    def _on_speed_change(self, value):
        """CW speed slider changed - with throttling"""
        wpm = int(float(value))
        self.speed_label.config(text=f"{wpm} WPM")
        logger.debug(f"[GUI] Speed slider moved to {wpm} WPM")
        
        # Cancel pending update
        if self._speed_update_pending:
            self.root.after_cancel(self._speed_update_pending)
            logger.debug(f"[GUI] Cancelled pending speed update")
        
        # Schedule update after 300ms of no changes (debounce)
        self._speed_update_pending = self.root.after(300, lambda: self._apply_speed_change(wpm))
        logger.debug(f"[GUI] Scheduled speed update in 300ms")
    
    def _apply_speed_change(self, wpm):
        """Actually apply speed change (called after debounce delay)"""
        logger.info(f"[GUI] Applying speed change to {wpm} WPM")
        
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.controller.update_cw_speed(wpm),
                self.loop
            )
            # Check for exceptions after 1 second
            self.root.after(1000, lambda: self._check_future_result(future, "speed update"))
        except Exception as e:
            logger.error(f"[GUI] Error queueing speed update: {e}")
        
        self._speed_update_pending = None
        
    def _on_freq_change(self, value):
        """Sidetone frequency slider changed - with throttling"""
        freq = int(float(value))
        self.freq_label.config(text=f"{freq} Hz")
        logger.debug(f"[GUI] Frequency slider moved to {freq} Hz")
        
        # Cancel pending update
        if self._sidetone_update_pending:
            self.root.after_cancel(self._sidetone_update_pending)
            logger.debug(f"[GUI] Cancelled pending sidetone update")
        
        # Schedule update after 300ms of no changes (debounce)
        self._sidetone_update_pending = self.root.after(300, lambda: self._apply_sidetone_change())
        logger.debug(f"[GUI] Scheduled sidetone update in 300ms")
    
    def _on_volume_change(self, value):
        """Sidetone volume slider changed - with throttling"""
        vol_percent = float(value)
        self.vol_label.config(text=f"{vol_percent:.0f}%")
        
        # Cancel pending update
        if self._sidetone_update_pending:
            self.root.after_cancel(self._sidetone_update_pending)
        
        # Schedule update after 300ms of no changes (debounce)
        self._sidetone_update_pending = self.root.after(300, lambda: self._apply_sidetone_change())
    
    def _apply_sidetone_change(self):
        """Actually apply sidetone changes (called after debounce delay)"""
        freq = self.sidetone_freq_var.get()
        vol = self.sidetone_vol_var.get() / 100.0
        
        logger.info(f"[GUI] Applying sidetone change: {freq} Hz, {vol*100:.0f}%")
        
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.controller.update_sidetone(freq, vol),
                self.loop
            )
            # Check for exceptions after 1 second
            self.root.after(1000, lambda: self._check_future_result(future, "sidetone update"))
        except Exception as e:
            logger.error(f"[GUI] Error updating sidetone: {e}")
        
        self._sidetone_update_pending = None
    
    def _check_future_result(self, future, operation_name):
        """Check if async operation completed successfully"""
        try:
            if future.done():
                result = future.result()  # This will raise exception if coroutine failed
                logger.debug(f"[GUI] {operation_name} completed successfully")
            else:
                logger.warning(f"[GUI] {operation_name} still pending after 1 second")
        except Exception as e:
            logger.error(f"[GUI] {operation_name} failed: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to apply {operation_name}:\\n{str(e)}")
            
    def _save_config(self):
        """Save current config to file"""
        # Update config dict from UI widgets
        self.config['operator']['callsign'] = self.callsign_var.get().upper()
        self.config['cw']['keyer_mode'] = self.keyer_mode_var.get()
        
        for fkey, text_widget in self.macro_entries.items():
            self.config['function_keys'][fkey] = text_widget.get()
            
        self.config['cw']['speed_wpm'] = self.cw_speed_var.get()
        self.config['sidetone']['frequency'] = self.sidetone_freq_var.get()
        self.config['sidetone']['volume'] = self.sidetone_vol_var.get() / 100.0
        
        # Sync Vail adapter settings with main CW settings (if vail_adapter section exists)
        if 'vail_adapter' in self.config:
            self.config['vail_adapter']['speed_wpm'] = self.cw_speed_var.get()
            # Note: keyer_mode is now derived from cw.keyer_mode in main.py
        
        # Save via controller
        future = asyncio.run_coroutine_threadsafe(
            self.controller.save_config_to_file(self.config),
            self.loop
        )
        
        try:
            future.result(timeout=2.0)
            messagebox.showinfo("Config Saved", "Configuration saved to config.yaml")
            logger.info("[GUI] Config saved successfully")
        except Exception as e:
            messagebox.showerror("Save Failed", f"Error saving config: {e}")
            logger.error(f"[GUI] Config save failed: {e}")
            
    def _reload_config(self):
        """Reload config from file"""
        result = messagebox.askyesno("Reload Config", 
                                     "Reload config from file? Unsaved changes will be lost.")
        if not result:
            return
            
        try:
            # Reload via controller
            future = asyncio.run_coroutine_threadsafe(
                self.controller.reload_config(),
                self.loop
            )
            
            new_config = future.result(timeout=2.0)
            if new_config is None:
                raise ValueError("Controller returned None (may be shutting down)")
            
            self.config = new_config.copy()
            
            # Update UI widgets
            self.callsign_var.set(self.config['operator']['callsign'])
            self.keyer_mode_var.set(self.config['cw']['keyer_mode'])
            
            for fkey, text_widget in self.macro_entries.items():
                text_widget.delete(0, tk.END)
                text_widget.insert(0, self.config['function_keys'].get(fkey, ""))
                
            # Update slider values
            self.cw_speed_var.set(self.config['cw']['speed_wpm'])
            self.sidetone_freq_var.set(self.config['sidetone']['frequency'])
            self.sidetone_vol_var.set(self.config['sidetone']['volume'] * 100)
            
            # Update slider labels manually (set() doesn't trigger callbacks)
            self.speed_label.config(text=f"{self.config['cw']['speed_wpm']} WPM")
            self.freq_label.config(text=f"{self.config['sidetone']['frequency']} Hz")
            self.vol_label.config(text=f"{self.config['sidetone']['volume']*100:.0f}%")
            
            # Actually apply the reloaded settings to hardware/TCI
            logger.info("[GUI] Applying reloaded settings to hardware...")
            try:
                # Apply speed
                asyncio.run_coroutine_threadsafe(
                    self.controller.update_cw_speed(self.config['cw']['speed_wpm']),
                    self.loop
                )
                
                # Apply sidetone
                asyncio.run_coroutine_threadsafe(
                    self.controller.update_sidetone(
                        self.config['sidetone']['frequency'],
                        self.config['sidetone']['volume']
                    ),
                    self.loop
                )
            except Exception as e:
                logger.error(f"[GUI] Error applying reloaded settings: {e}")
            
            messagebox.showinfo("Config Reloaded", "Configuration reloaded from config.yaml")
            logger.info("[GUI] Config reloaded successfully")
        except Exception as e:
            messagebox.showerror("Reload Failed", f"Error reloading config: {e}")
            logger.error(f"[GUI] Config reload failed: {e}")
            
    def _quit(self):
        """Quit application"""
        result = messagebox.askyesno("Quit", "Quit TCI CW Controller?")
        if result:
            logger.info("[GUI] User quit via GUI")
            self.root.quit()
            
    # Status updates
    
    def _update_status(self):
        """Poll controller state and update status indicators"""
        try:
            # TCI connection status
            if self.controller.tci_client and self.controller.tci_client.ready:
                self.tci_status_var.set("Connected")
                self.tci_indicator.config(bg="green")
            else:
                self.tci_status_var.set("Disconnected")
                self.tci_indicator.config(bg="red")
                
            # USB paddle status and device type
            if self.controller.usb_paddle_handler and self.controller.usb_paddle_handler.running:
                self.usb_status_var.set("Connected")
                self.usb_indicator.config(bg="green")
                
                # Determine device type
                vail_enabled = self.config.get('vail_adapter', {}).get('enabled', False)
                if vail_enabled:
                    self.usb_device_var.set("Vail Adapter")
                else:
                    self.usb_device_var.set("Legacy (Python iambic)")
            else:
                self.usb_status_var.set("Disconnected")
                self.usb_indicator.config(bg="red")
                self.usb_device_var.set("None")
            
            # Update active LED indicator (green when paddle or macro active, gray when idle)
            paddle_active = hasattr(self.controller, 'paddle_ptt_active') and self.controller.paddle_ptt_active
            macro_active = hasattr(self.controller, 'macro_active') and self.controller.macro_active
            
            if paddle_active or macro_active:
                self.active_macro_indicator.config(fg="#00ff00")
            else:
                self.active_macro_indicator.config(fg="gray")
                
        except Exception as e:
            logger.error(f"[GUI] Status update error: {e}")
            
        # Schedule next update (100ms)
        self.root.after(100, self._update_status)
        
    # Asyncio integration
    
    def _schedule_asyncio_step(self):
        """Run pending asyncio tasks (polling integration)"""
        try:
            # Process pending tasks without blocking
            # Note: This requires the event loop to be running in a background thread
            # The loop itself is managed by main.py's asyncio.run(controller.run())
            pass  # No-op - event loop runs independently in background
        except Exception as e:
            logger.error(f"[GUI] Asyncio step error: {e}")
            
        # Schedule next step (50ms for responsive async tasks)
        self.root.after(50, self._schedule_asyncio_step)
        
    def _schedule_status_update(self):
        """Start status polling"""
        self._update_status()
        
    def run(self):
        """Start GUI main loop"""
        logger.info("[GUI] Starting Tkinter main loop")
        self.root.mainloop()
        logger.info("[GUI] Tkinter main loop exited")
