"""The installer window: python -m installer.gui

A front end over the CLI (docs/installer.md): it collects the game files, the keys file path, the game language and the
install folder, runs `python -m installer ...` as a subprocess and shows its JSON events. Decisions live in guilogic.py;
this file only draws. The window text is in installer/text/<en|ru>.json under "ui.*" and "launcher.*" ids.

    --selftest [--shots DIR]   build every screen in both languages and close by itself (PNG per screen with --shots)
    --lang en|ru               window language (default: the Windows display language)
"""
import argparse
import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import ImageGrab   # only the screenshots of --selftest --shots need it; the release has no PIL
except ImportError:
    ImageGrab = None

from . import ini
from . import layout as layouts
from .guilogic import (DONE, FAILED, PENDING, RUNNING, SKIPPED, UI_LANGUAGES, Catalog, Form, Model, Process,
                       amount, default_game_language, describe_event, parse_line, windows_ui_language)

POLL_MS = 100
GLYPH = {PENDING: '\u25cb', RUNNING: '\u25b6', DONE: '\u2714', SKIPPED: '\u2013', FAILED: '\u2716'}
COLOR = {PENDING: '#808080', RUNNING: '#1a5fb4', DONE: '#26a269', SKIPPED: '#808080', FAILED: '#c01c28'}
PAD = 14


def fix_tcl_paths():
    """The embeddable Python keeps Tcl/Tk next to python.exe (python/tcl); point Tcl at it when it is not found."""
    tcl = Path(sys.executable).resolve().parent / 'tcl'
    if tcl.is_dir() and 'TCL_LIBRARY' not in os.environ:
        for lib, var in (('tcl9.0', 'TCL_LIBRARY'), ('tk9.0', 'TK_LIBRARY')):
            if (tcl / lib).is_dir():
                os.environ[var] = str(tcl / lib)


class App:
    catalog_type = Catalog
    ui_languages = UI_LANGUAGES
    icon_name = 'uo.ico'

    def __init__(self, root, ui_lang, layout, run_args=None):
        self.root, self.layout = root, layout
        self.cat = self.catalog_type(ui_lang)
        self.model = Model()
        self.process = None
        self.closing = False
        self.run_args = run_args or {}   # extra CLI options for tests: temp, verify
        self.log_entries = []            # events and raw lines of the run, rendered by render_log
        self.bound = []                  # (widget, option, id, params) retranslated on a language switch
        # text wraps at the design width scaled for this display, so a 150 % screen keeps the layout; fit() widens it
        # to the window, since a wide window should not stack a paragraph in a narrow column
        self.dpi = getattr(root, '_engage_dpi_factor', 1.0) or 1.0
        self.wrap = int(round(620 * self.dpi))
        self.wrap_labels = []
        self.vars = {n: tk.StringVar() for n in ('base', 'update', 'keys', 'dest')}
        self.vars['lang'] = tk.StringVar()
        self.lang_code = default_game_language(ui_lang)
        self.screen = None
        self.fitted = None               # the (width, height) fit last gave the window

        root.title(self.cat('ui.title'))
        icon = layout.runtime / self.icon_name
        png = layout.runtime / 'uo.png'   # Tk loads no .ico off Windows; the Linux release converts it
        if os.name == 'nt' and icon.is_file():
            root.iconbitmap(default=str(icon))
        elif png.is_file():
            self.icon = tk.PhotoImage(file=str(png))
            root.iconphoto(True, self.icon)
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.ui_font = 'Segoe UI' if os.name == 'nt' else 'DejaVu Sans'
        self.mono_font = 'Consolas' if os.name == 'nt' else 'DejaVu Sans Mono'
        style = ttk.Style(root)
        style.configure('Title.TLabel', font=(self.ui_font, 15, 'bold'))
        style.configure('Notice.TLabel', background='#fff3cd', foreground='#664d03', padding=8)
        style.configure('Err.TLabel', foreground='#c01c28')
        style.configure('Version.TLabel', foreground='#808080')

        top = ttk.Frame(root, padding=(PAD, PAD, PAD, 0))
        top.pack(fill='x')
        self.top = top
        self.title = self.label(top, 'ui.title', style='Title.TLabel')
        self.title.pack(side='left')
        version = ttk.Label(top, style='Version.TLabel')
        self.bind_text(version, 'text', 'ui.version', version=layout.release)
        version.pack(side='left', padx=(10, 0), pady=(6, 0))
        sw = ttk.Frame(top)
        sw.pack(side='right')
        self.ui_var = tk.StringVar(value=self.cat.lang)
        for code in self.ui_languages:
            ttk.Radiobutton(sw, text=code.upper(), value=code, variable=self.ui_var, style='Toolbutton',
                            command=self.switch_language).pack(side='left', padx=1)
        # The body scrolls: at 200 % display scaling the form is taller than a 2160-pixel screen, and a window that
        # grows past the screen hides its own Install button. The canvas holds the frame, a scrollbar appears only when
        # the content does not fit, and the wheel scrolls it.
        self.body_outer = ttk.Frame(root)
        self.body_outer.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(self.body_outer, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self.body_outer, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.canvas, padding=PAD)
        self.body_window = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', self.on_body_resize)
        self.canvas.bind('<Configure>', self.on_canvas_resize)
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self.canvas.bind_all(sequence, self.on_wheel, add='+')
        self.frames = {'form': self.build_form(), 'run': self.build_run(), 'finish': self.build_finish()}
        self.vars['lang'].set(self.lang_label(self.lang_code))
        self.vars['dest'].trace_add('write', lambda *_: self.refresh_form())
        self.show('form')

    # ---- text -----------------------------------------------------------------------------------------------------

    def bind_text(self, widget, option, id, **params):
        self.bound.append((widget, option, id, params))
        widget.configure(**{option: self.cat(id, **params)})

    def label(self, parent, id, **kw):
        w = ttk.Label(parent, **kw)
        self.bind_text(w, 'text', id)
        return w

    def button(self, parent, id, command, **kw):
        w = ttk.Button(parent, command=command, **kw)
        self.bind_text(w, 'text', id)
        return w

    def retext(self):
        self.root.title(self.cat('ui.title'))
        for widget, option, id, params in self.bound:
            widget.configure(**{option: self.cat(id, **params)})
        self.fill_languages()
        self.refresh_form()
        self.refresh_run()
        self.refresh_finish()
        self.render_log()

    def switch_language(self):
        self.cat.set_language(self.ui_var.get())
        code = self.selected_lang()
        self.retext()
        self.vars['lang'].set(self.lang_label(code))
        self.fit()

    def lang_label(self, code):
        return self.cat('lang.' + code)

    def fill_languages(self):
        code = self.selected_lang()
        self.lang_box.configure(values=[self.lang_label(c) for c in ini.LANGUAGES])
        self.vars['lang'].set(self.lang_label(code))

    def selected_lang(self):
        """The game language code behind the combobox text (kept when it is not one of the labels)."""
        shown = self.vars['lang'].get()
        for code in ini.LANGUAGES:
            for lang in UI_LANGUAGES:
                if shown == Catalog(lang)('lang.' + code):
                    return code
        return self.lang_code

    # ---- screens --------------------------------------------------------------------------------------------------

    def show(self, name):
        for f in self.frames.values():
            f.pack_forget()
        self.frames[name].pack(fill='both', expand=True)
        self.screen = name
        self.fit()

    def fit(self):
        """Size the window to its content, clamped to the screen. The body scrolls when the content is taller than the
        window, so at 200 % display scaling the form stays usable instead of growing past the screen and hiding its own
        buttons. The window only grows: opening the log makes it taller, and a size the user dragged it to stays."""
        self.root.update_idletasks()
        self.apply_wrap()
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        # The body's requested height, not the root's: the canvas no longer reports the frame's height upward, so the
        # content size has to come from the frame itself.
        content = max(f.winfo_reqheight() for f in self.frames.values())
        w = max(self.root.winfo_reqwidth(), self.body.winfo_reqwidth() + 2 * PAD)
        h = self.top.winfo_reqheight() + content + 2 * PAD
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = min(w, screen_w - 40), min(h, screen_h - 80)
        if (w, h) == self.fitted:   # refresh_run calls it on every poll: leave the window alone while nothing grew
            return
        self.fitted = (w, h)
        self.root.minsize(min(w, screen_w - 40), min(h, screen_h - 80))
        self.root.geometry(f'{w}x{h}')
        self.root.update_idletasks()
        self.apply_wrap()
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        self.update_scrollbar()

    def on_body_resize(self, event):
        """Keep the wrapped labels as wide as the body, and the scroll region as tall as the content."""
        if event.widget is self.body:
            self.apply_wrap(event.width - 2 * PAD)
            self.canvas.configure(scrollregion=self.canvas.bbox('all'))
            self.update_scrollbar()

    def on_canvas_resize(self, event):
        """The body fills the canvas width, so the text wraps to the window and the content is as tall as it needs."""
        self.canvas.itemconfigure(self.body_window, width=event.width)
        self.apply_wrap(event.width - 2 * PAD)
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        self.update_scrollbar()

    def update_scrollbar(self):
        """Show the scrollbar only when the content is taller than the viewport."""
        needed = self.body.winfo_reqheight() > self.canvas.winfo_height() + 2
        if needed and not self.scrollbar.winfo_ismapped():
            self.scrollbar.pack(side='right', fill='y')
        elif not needed and self.scrollbar.winfo_ismapped():
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)

    def on_wheel(self, event):
        """Scroll the body with the wheel when the pointer is over it."""
        if not self.scrollbar.winfo_ismapped():
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None or not str(widget).startswith(str(self.body_outer)):
            return
        if getattr(event, 'num', None) == 4:
            step = -1
        elif getattr(event, 'num', None) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step * 3, 'units')

    def track_wrap(self, widget):
        """Wrap this label to the body's width whenever the window is resized, instead of a fixed design width."""
        widget.configure(wraplength=self.wrap)   # the design width until fit() knows the real one
        self.wrap_labels.append(widget)
        return widget

    def apply_wrap(self, width=None):
        """Set every tracked label's wraplength to the width it actually has room for."""
        width = width or max(self.body.winfo_width() - 2 * PAD, 320)
        for widget in self.wrap_labels:
            try:
                widget.configure(wraplength=width)
            except tk.TclError:
                pass

    def notice(self, parent):
        w = ttk.Label(parent, style='Notice.TLabel', justify='left')
        self.bind_text(w, 'text', 'ui.notice')
        return self.track_wrap(w)

    def build_form(self):
        f = ttk.Frame(self.body)
        bar = ttk.Frame(f)   # packed first so pack shrinks the text above it, never the buttons
        bar.pack(side='bottom', fill='x', pady=(12, 0))
        self.start_button = self.button(bar, 'ui.start', self.start)
        self.start_button.pack(side='right')
        self.form_intro = self.track_wrap(self.label(f, 'ui.intro', justify='left'))
        self.form_intro.pack(anchor='w', pady=(0, 8))
        self.notice(f).pack(fill='x', pady=(0, 10))
        grid = ttk.Frame(f)
        grid.pack(fill='x')
        grid.columnconfigure(1, weight=1)
        rows = (('base', 'ui.base', 'ui.pick_game'), ('update', 'ui.update', 'ui.pick_update'),
                ('keys', 'ui.keys', 'ui.pick_keys'))
        self.form_rows = {}
        for i, (name, label_id, pick_id) in enumerate(rows):
            lab = self.label(grid, label_id)
            lab.grid(row=i, column=0, sticky='w', pady=4, padx=(0, 10))
            ttk.Entry(grid, textvariable=self.vars[name]).grid(row=i, column=1, sticky='ew', pady=4)
            b = self.button(grid, 'ui.browse', lambda n=name, p=pick_id: self.pick_file(n, p))
            b.grid(row=i, column=2, padx=(8, 0))
            self.form_rows[name] = lab
        i = len(rows)
        self.label(grid, 'ui.game_language').grid(row=i, column=0, sticky='w', pady=4)
        self.lang_box = ttk.Combobox(grid, textvariable=self.vars['lang'], state='readonly', width=30)
        self.lang_box.grid(row=i, column=1, sticky='w', pady=4)
        self.lang_touched = False
        self.lang_box.bind('<<ComboboxSelected>>', lambda _e: setattr(self, 'lang_touched', True))
        self.fill_languages()
        self.label(grid, 'ui.dest').grid(row=i + 1, column=0, sticky='w', pady=4)
        ttk.Entry(grid, textvariable=self.vars['dest']).grid(row=i + 1, column=1, sticky='ew', pady=4)
        self.button(grid, 'ui.browse', self.pick_dest).grid(row=i + 1, column=2, padx=(8, 0))
        self.update_hint = self.track_wrap(ttk.Label(f, justify='left'))
        self.update_hint.pack(anchor='w', pady=(8, 0))
        self.form_error = self.track_wrap(ttk.Label(f, style='Err.TLabel', justify='left'))
        self.form_error.pack(anchor='w', pady=(8, 0))
        self.track_wrap(self.label(f, 'ui.keys_private', justify='left')).pack(anchor='w', pady=(8, 0))
        return f

    def build_run(self):
        f = ttk.Frame(self.body)
        bar = ttk.Frame(f)
        bar.pack(side='bottom', fill='x', pady=(8, 0))
        self.cancel_button = self.button(bar, 'ui.cancel', self.cancel)
        self.cancel_button.pack(side='right')
        self.log_toggle = self.button(bar, 'ui.show_log', self.toggle_log)
        self.log_toggle.pack(side='left')
        self.run_header = ttk.Label(f, font=(self.ui_font, 11, 'bold'))
        self.run_header.pack(anchor='w')
        self.step_rows = {}
        self.steps_box = ttk.Frame(f)
        self.steps_box.pack(fill='x', pady=(8, 8))
        self.phase_label = self.track_wrap(ttk.Label(f, justify='left'))
        self.phase_label.pack(anchor='w')
        self.bar = ttk.Progressbar(f, mode='determinate', maximum=1000)
        self.bar.pack(fill='x', pady=(4, 6))
        self.space_label = self.track_wrap(ttk.Label(f, justify='left'))
        self.space_label.pack(anchor='w')
        self.warn_label = self.track_wrap(ttk.Label(f, justify='left', foreground='#9a6700'))
        self.warn_label.pack(anchor='w')
        self.log_frame = ttk.Frame(f)
        self.log = tk.Text(self.log_frame, height=10, wrap='word', state='disabled', font=(self.mono_font, 9))
        scroll = ttk.Scrollbar(self.log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)
        self.log_open = False
        return f

    def build_finish(self):
        f = ttk.Frame(self.body)
        bar = ttk.Frame(f)
        bar.pack(side='bottom', fill='x', pady=(12, 0))
        self.button(bar, 'ui.close', self.on_close).pack(side='right')
        self.open_button = self.button(bar, 'ui.open_folder', self.open_folder)
        self.open_button.pack(side='right', padx=(0, 8))
        self.back_button = self.button(bar, 'ui.back', lambda: self.show('form'))
        self.back_button.pack(side='left')
        self.finish_title = self.track_wrap(ttk.Label(f, style='Title.TLabel', justify='left'))
        self.finish_title.pack(anchor='w')
        self.finish_text = self.track_wrap(ttk.Label(f, justify='left'))
        self.finish_text.pack(anchor='w', pady=(8, 8))
        self.finish_steps = ttk.Frame(f)
        self.finish_steps.pack(fill='x', pady=(0, 8))
        self.finish_notice = self.notice(f)
        self.finish_notice.pack(fill='x', pady=(8, 0))
        return f

    # ---- form -----------------------------------------------------------------------------------------------------

    def form(self):
        return Form(base=self.vars['base'].get(), update=self.vars['update'].get(), keys=self.vars['keys'].get(),
                    lang=self.selected_lang(), dest=self.vars['dest'].get())

    def refresh_form(self):
        form = self.form()
        state = form.installed_state()
        if state is not None:
            self.update_hint.configure(text=self.cat('ui.update_mode'))
            recorded = ini.canonical_language(state.get('lang'))
            if recorded in ini.LANGUAGES and not self.lang_touched:
                self.vars['lang'].set(self.lang_label(recorded))
        else:
            self.update_hint.configure(text='')
        self.fit()

    def pick_file(self, name, pick_id):
        types = [(self.cat('ui.type_game'), '*.nsp *.xci'), (self.cat('ui.type_all'), '*.*')]
        if name == 'update':
            types = [(self.cat('ui.type_nsp'), '*.nsp'), (self.cat('ui.type_all'), '*.*')]
        if name == 'keys':
            types = [(self.cat('ui.type_keys'), '*.keys'), (self.cat('ui.type_all'), '*.*')]
        path = filedialog.askopenfilename(title=self.cat(pick_id), filetypes=types)
        if path:
            self.vars[name].set(os.path.normpath(path))

    def pick_dest(self):
        path = filedialog.askdirectory(title=self.cat('ui.pick_dest'), mustexist=False)
        if path:
            self.vars['dest'].set(os.path.normpath(path))

    # ---- run ------------------------------------------------------------------------------------------------------

    def start(self):
        form = self.form()
        problems = form.problems()
        if problems:
            self.form_error.configure(text=' '.join(self.cat(p) for p in problems))
            self.fit()
            return
        self.form_error.configure(text='')
        command = form.command(self.layout.python, root=self.layout.root if self.layout.dev else None,
                               temp=self.run_args.get('temp'), verify=self.run_args.get('verify', False))
        self.begin(command, self.layout.root)

    def begin(self, command, cwd):
        self.model = Model()
        self.model.dest = self.vars['dest'].get().strip()
        self.clear_log()
        self.build_step_rows(self.model.steps)
        self.cancel_button.configure(state='normal')
        self.bound = [b for b in self.bound if b[0] is not self.cancel_button]
        self.bind_text(self.cancel_button, 'text', 'ui.cancel')
        self.show('run')
        self.refresh_run()
        self.process = Process(command, cwd=cwd, helper_python=self.layout.python)
        self.root.after(POLL_MS, self.poll)

    def build_step_rows(self, steps):
        for w in self.steps_box.winfo_children():
            w.destroy()
        self.step_rows = {}
        for s in steps:
            row = ttk.Frame(self.steps_box)
            row.pack(fill='x', pady=1)
            glyph = ttk.Label(row, width=3)
            glyph.pack(side='left')
            name = ttk.Label(row)
            name.pack(side='left')
            self.step_rows[s] = (glyph, name)

    def poll(self):
        if self.process is None:
            return
        try:
            while True:
                line = self.process.lines.get_nowait()
                if line is not None:
                    self.feed(line)
        except Exception:
            pass
        code = self.process.poll()
        self.refresh_run()
        if code is None:
            self.root.after(POLL_MS, self.poll)
            return
        self.model.finish(code)
        self.process = None
        self.refresh_run()
        self.refresh_finish()
        self.show('finish')
        if self.closing:
            self.root.destroy()

    def feed(self, line):
        """One stdout line of the CLI: an event updates the model and the log, anything else goes to the log as is."""
        event = parse_line(line)
        if event is None:
            if line.strip():
                self.log_entries.append(line.rstrip())
                self.append_log(line.rstrip())
            return
        before = list(self.model.steps)
        self.model.apply(event)
        if self.model.steps != before:
            self.build_step_rows(self.model.steps)
        self.log_entries.append(event)
        self.append_log(describe_event(self.cat, event))

    def refresh_run(self):
        m = self.model
        for s, (glyph, name) in self.step_rows.items():
            state = m.states.get(s, PENDING)
            glyph.configure(text=GLYPH[state], foreground=COLOR[state])
            name.configure(text=self.cat('step.' + s) + (f'  ({self.cat("ui.state_" + state)})'
                                                          if state in (SKIPPED, FAILED) else ''),
                           font=(self.ui_font, 10, 'bold') if state == RUNNING else (self.ui_font, 10))
        mode = m.mode or 'install'
        self.run_header.configure(text=self.cat('ui.running_' + mode if mode in ('install', 'resume', 'update')
                                                else 'ui.running_install'))
        if m.current and m.phase:
            text = self.cat(f'phase.{m.current}.{m.phase}')
            if m.total:
                text += '  ' + amount(self.cat, m.done, m.total, m.unit)
        elif m.current:
            text = self.cat('step.' + m.current)
        else:
            text = ''
        self.phase_label.configure(text=text)
        frac = m.fraction()
        if frac is None:
            if str(self.bar.cget('mode')) != 'indeterminate':
                self.bar.configure(mode='indeterminate')
                self.bar.start(15)
        else:
            if str(self.bar.cget('mode')) == 'indeterminate':
                self.bar.stop()
                self.bar.configure(mode='determinate')
            self.bar.configure(value=frac * 1000)
        self.space_label.configure(text=m.need_text(self.cat))
        self.warn_label.configure(text='\n'.join(self.cat(i, **p) for i, p in m.warnings))
        self.fit()   # new step rows and warnings make the screen taller

    def toggle_log(self):
        self.log_open = not self.log_open
        if self.log_open:
            self.log_frame.pack(fill='both', expand=True, pady=(8, 0))
        else:
            self.log_frame.pack_forget()
        self.bound = [b for b in self.bound if b[0] is not self.log_toggle]
        self.bind_text(self.log_toggle, 'text', 'ui.hide_log' if self.log_open else 'ui.show_log')
        self.fit()

    def clear_log(self):
        self.log_entries = []
        self.render_log()

    def render_log(self):
        """The whole log from the kept events in the current language (raw CLI lines stay as they are)."""
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        for entry in self.log_entries:
            text = entry if isinstance(entry, str) else describe_event(self.cat, entry)
            if text:
                self.log.insert('end', text + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def append_log(self, text):
        if text:
            self.log.configure(state='normal')
            self.log.insert('end', text + '\n')
            self.log.see('end')
            self.log.configure(state='disabled')

    def cancel(self):
        if self.process:
            self.cancel_button.configure(state='disabled')
            self.bound = [b for b in self.bound if b[0] is not self.cancel_button]
            self.bind_text(self.cancel_button, 'text', 'ui.cancelling')
            self.process.cancel()

    def on_close(self):
        if self.process:
            if not messagebox.askyesno(self.cat('ui.title'), self.cat('ui.confirm_cancel')):
                return
            self.closing = True
            self.cancel()
            return
        self.root.destroy()

    # ---- finish ---------------------------------------------------------------------------------------------------

    def refresh_finish(self):
        m = self.model
        result = m.result or 'ok'
        self.finish_title.configure(text=self.cat('ui.result_' + result))
        if result == 'ok':
            text = self.cat('ui.done_text', dest=m.dest)
        elif result == 'cancelled':
            text = self.cat('ui.cancelled_text')
        else:
            err = m.error or ('ui.process_died', {'code': m.exit_code}, None)
            text = self.cat(err[0], **err[1])
            if m.log_path:
                text += '\n\n' + self.cat('ui.log_file', path=m.log_path)
        self.finish_text.configure(text=text)
        for w in self.finish_steps.winfo_children():
            w.destroy()
        for s in m.steps:
            state = m.states.get(s, PENDING)
            ttk.Label(self.finish_steps, text=f'{GLYPH[state]}  {self.cat("step." + s)}',
                      foreground=COLOR[state]).pack(anchor='w')
        if result == 'ok':
            self.open_button.state(['!disabled'])
        else:
            self.open_button.state(['disabled' if not Path(m.dest or '.').is_dir() else '!disabled'])

    def open_folder(self):
        dest = self.model.dest
        if dest and Path(dest).is_dir():
            if os.name == 'nt':
                os.startfile(dest)
            else:
                subprocess.Popen(['xdg-open', dest], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)


# ---- self test ---------------------------------------------------------------------------------------------------

SAMPLE_START = {'type': 'start', 'release': 'selftest', 'mode': 'install', 'dest': 'Z:/selftest/UO', 'log': 'D:\\log.txt',
                'steps': ['check', 'extract', 'lift', 'compile', 'prepare', 'cleanup']}
SAMPLE_RUN = [
    SAMPLE_START,
    {'type': 'plan', 'run': ['check', 'extract'], 'skip': {}},
    {'type': 'step', 'step': 'check', 'state': 'started'},
    {'type': 'progress', 'step': 'check', 'phase': 'inputs'},
    {'type': 'space', 'dest': 'Z:/selftest/UO', 'need': 36_400_000_000, 'free': 210_000_000_000, 'temp': 'C:\\t',
     'temp_need': 0, 'temp_free': 0, 'same_disk': True},
    {'type': 'warning', 'id': 'warn.lang_needs_fonts', 'params': {'lang': 'ko'}},
    {'type': 'step', 'step': 'check', 'state': 'done', 'seconds': 12.0},
    {'type': 'step', 'step': 'extract', 'state': 'started'},
    {'type': 'progress', 'step': 'extract', 'phase': 'copy', 'done': 6_000_000_000, 'total': 16_000_000_000,
     'unit': 'bytes'},
]


SAMPLE_OK = [SAMPLE_START, {'type': 'plan', 'run': ['check', 'extract'], 'skip': {'lift': 'current', 'compile': 'current',
                                                                       'prepare': 'current', 'cleanup': 'not_needed'}},
             {'type': 'step', 'step': 'check', 'state': 'started'},
             {'type': 'step', 'step': 'check', 'state': 'done', 'seconds': 1.0},
             {'type': 'step', 'step': 'extract', 'state': 'started'},
             {'type': 'step', 'step': 'extract', 'state': 'done', 'seconds': 1.0},
             {'type': 'step', 'step': 'lift', 'state': 'skipped', 'reason': 'current'},
             {'type': 'step', 'step': 'compile', 'state': 'skipped', 'reason': 'current'},
             {'type': 'step', 'step': 'prepare', 'state': 'skipped', 'reason': 'current'},
             {'type': 'step', 'step': 'cleanup', 'state': 'done', 'seconds': 0.1},
             {'type': 'end', 'result': 'ok', 'seconds': 3}]


def grab(root, path):
    if ImageGrab is None:
        raise SystemExit('--shots needs Pillow')
    root.attributes('-topmost', True)
    root.update()
    x, y, w, h = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(path)


def selftest(layout, shots=None):
    """Every screen in both languages; the window closes itself. Returns 0 when nothing raised."""
    fix_tcl_paths()
    root = tk.Tk()
    root.geometry('+60+40')
    app = App(root, 'en', layout)
    steps, mixed = [], []

    def shot(name):
        root.update()
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            grab(root, Path(shots) / f'{name}.png')

    def run_all():
        for lang in UI_LANGUAGES:
            app.ui_var.set(lang)
            app.switch_language()
            tag = lang
            app.vars['dest'].set('Z:/selftest/UO')
            app.vars['base'].set('Z:/selftest/uo.xci')
            app.vars['update'].set('Z:/selftest/uo-1.0.5.nsp')
            app.vars['keys'].set('Z:/selftest/prod.keys')
            app.show('form')
            app.start()   # missing files: shows the problems
            steps.append(app.form_error.cget('text'))
            shot(f'form-{tag}')
            app.model = Model()
            app.clear_log()
            app.build_step_rows(app.model.steps)
            app.show('run')
            for e in SAMPLE_RUN:
                app.feed(json.dumps(e))
            app.log_open = False
            app.toggle_log()
            app.refresh_run()
            shot(f'run-{tag}')
            if lang == 'en':   # the log follows the window language: no English line may remain after the switch
                app.ui_var.set('ru')
                app.switch_language()
                text = app.log.get('1.0', 'end')
                mixed.append('Checking' in text or 'Extracting' not in text and 'Распаковка' not in text)
                app.ui_var.set('en')
                app.switch_language()
            app.model = Model()
            for e in SAMPLE_OK:
                app.model.apply(e)
            app.model.dest = 'Z:/selftest/UO'
            app.model.finish(0)
            app.refresh_finish()
            app.show('finish')
            shot(f'finish-ok-{tag}')
            app.model = Model()
            for e in SAMPLE_RUN[:3] + [{'type': 'error', 'id': 'check.disk_space', 'step': 'check',
                                        'params': {'path': 'Z:/selftest/UO', 'need_gb': 36.4, 'free_gb': 20.1}},
                                       {'type': 'end', 'result': 'failed', 'seconds': 3}]:
                app.model.apply(e)
            app.model.finish(1)
            app.refresh_finish()
            shot(f'finish-failed-{tag}')
            app.model = Model()
            app.model.finish(3)
            app.refresh_finish()
            shot(f'finish-cancelled-{tag}')
            app.toggle_log()
        root.destroy()

    root.after(200, run_all)
    root.after(60000, root.destroy)   # never leaves a window open
    root.mainloop()
    return 0 if len(steps) == len(UI_LANGUAGES) and all(steps) and mixed == [False] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--shots', type=Path)
    ap.add_argument('--lang', choices=UI_LANGUAGES)
    ap.add_argument('--root', type=Path)
    args = ap.parse_args(argv)
    layout = layouts.find(args.root)
    if args.selftest:
        return selftest(layout, args.shots)
    fix_tcl_paths()
    root = tk.Tk()
    App(root, args.lang or windows_ui_language(), layout)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
