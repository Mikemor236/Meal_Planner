import io
import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from urllib import error

from debuglog import _agent_log, _dbg
from meal_photos import _read_url, meal_lookup_name, meal_photo_bytes
from nutrition import format_nutrition, nutrition_per_serving
from playlists import (
    create_cooking_playlist,
    create_foo_fighters_playlist,
    create_oasis_playlist,
)
from recipes import (
    MAX_LEN,
    _BASE_SERVINGS,
    _VERSION_LABELS,
    _VERSION_ORDER,
    already_have,
    fetch_ingredients,
    ingredients_need_amounts,
    measure_ingredients,
    minutes_from_meal_text,
    owned_items,
    parse_two_meals,
    scale_ingredient,
    scale_method,
)
from spotify import (
    TOKEN_FILE,
    current_track,
    spotify_access_token,
    spotify_device_id,
    spotify_next,
    spotify_pause,
    spotify_play,
    spotify_playback,
    spotify_previous,
    spotify_shuffle,
    upcoming_tracks,
)

MAX_NOTES = 32
NOTES_FILE = Path(__file__).with_name("notes.txt")


class MealPlannerApp:
    def __init__(self, root):
        self.root = root
        self.notes = []
        self.last_meal = None
        self.cook_minutes = None
        self.last_playlist_id = None
        self.cover_image = None
        self.queue_images = []
        self._meal_photo = None
        self._meal_photo_key = None
        self._meal_photo_bytes = None
        self._polling = False
        self.busy = False
        self.shuffle_on = None
        self.base_ingredients = []
        self.base_method = ""
        self.recipe_notes = []
        self.current_servings = _BASE_SERVINGS
        self._setting_servings = False
        self.versions = {}
        self.selected_version = None
        self._nutrition_meal = None
        root.title("Meal_Planner")
        root.minsize(1080, 560)

        shell = ttk.Frame(root)
        shell.pack(fill=tk.BOTH, expand=True)
        self._build_sidebar(shell)
        panel = ttk.Frame(shell)
        panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(panel, height=8, activestyle="dotbox")
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 8))

        method_bar = ttk.Frame(panel)
        method_bar.pack(fill=tk.X, padx=12)
        ttk.Label(method_bar, text="Recipe").pack(side=tk.LEFT)
        self.servings_var = tk.StringVar(value=str(_BASE_SERVINGS))
        self.servings_box = ttk.Spinbox(
            method_bar,
            from_=1,
            to=12,
            width=4,
            textvariable=self.servings_var,
            command=self._servings_changed,
            state=tk.DISABLED,
        )
        self.servings_box.pack(side=tk.RIGHT)
        self.servings_box.bind("<Return>", self._servings_changed)
        self.servings_box.bind("<FocusOut>", self._servings_changed)
        ttk.Label(method_bar, text="Servings").pack(side=tk.RIGHT, padx=(8, 6))
        self.method = tk.Text(panel, height=6, wrap=tk.WORD, state=tk.DISABLED)
        self.method.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        ttk.Label(panel, text="Meal").pack(anchor=tk.W, padx=12)
        self.entry = ttk.Entry(panel)
        self.entry.pack(fill=tk.X, padx=12)

        buttons = ttk.Frame(panel)
        buttons.pack(fill=tk.X, padx=12, pady=8)
        self.buttons = []
        for label, command in (
            ("Print", self.print_notes),
            ("Meal", self.ask_meal),
            ("Playlist", self.ask_playlist),
            ("Foo Fighters", self.ask_foo_playlist),
            ("Oasis", self.ask_oasis_playlist),
        ):
            button = ttk.Button(buttons, text=label, command=command)
            button.pack(side=tk.LEFT, padx=(0, 6))
            self.buttons.append(button)
            if label == "Playlist":
                self.playlist_button = button
                button.configure(state=tk.DISABLED)
            if label == "Foo Fighters":
                self.foo_button = button
                button.configure(state=tk.DISABLED)
            if label == "Oasis":
                self.oasis_button = button
                button.configure(state=tk.DISABLED)

        now = ttk.Frame(panel)
        now.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.cover = ttk.Label(now)
        self.cover.pack(side=tk.LEFT, padx=(0, 8))
        self.now_text = ttk.Label(now, text="Nothing playing")
        self.now_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        transport = ttk.Frame(now)
        transport.pack(side=tk.RIGHT)
        self.playback_buttons = []
        for label, command in (
            ("Previous", self.playback_previous),
            ("Play", self.playback_play),
            ("Pause", self.playback_pause),
            ("Next", self.playback_next),
        ):
            button = ttk.Button(transport, text=label, command=command, width=8)
            button.pack(side=tk.LEFT, padx=(4, 0))
            self.playback_buttons.append(button)
        self.shuffle_button = ttk.Button(
            transport, text="Shuffle", command=self.playback_shuffle, width=11
        )
        self.shuffle_button.pack(side=tk.LEFT, padx=(4, 0))
        self.playback_buttons.append(self.shuffle_button)

        self.status = ttk.Label(panel, text="")
        self.status.pack(fill=tk.X, padx=12, pady=(0, 12))
        self.root.after(1000, self.refresh_now_playing)

    def _build_sidebar(self, shell):
        self._build_version_sidebar(shell)
        self.sidebar = tk.Frame(shell, width=300, bg="#e7b6d8")
        self.sidebar.pack(side=tk.RIGHT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        self.side_top = tk.Canvas(
            self.sidebar, height=188, bg="#e7b6d8", highlightthickness=0
        )
        self.side_top.pack(fill=tk.X)
        self.side_top.bind("<Configure>", self._paint_meal_header)

        self.card = tk.Frame(self.sidebar, bg="#c6e58a")
        self.card.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 16))

        self.peak = tk.Canvas(
            self.card, height=78, bg="#c6e58a", highlightthickness=0
        )
        self.peak.pack(fill=tk.X)
        self.peak.bind("<Configure>", self._draw_peak)

        holder = tk.Frame(self.card, bg="#c6e58a")
        holder.pack(fill=tk.BOTH, expand=True)
        self.queue_canvas = tk.Canvas(
            holder, bg="#c6e58a", highlightthickness=0
        )
        scroll = ttk.Scrollbar(
            holder, orient=tk.VERTICAL, command=self.queue_canvas.yview
        )
        self.queue_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.queue_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.queue_rows = tk.Frame(self.queue_canvas, bg="#c6e58a")
        self.queue_window = self.queue_canvas.create_window(
            (0, 0), window=self.queue_rows, anchor="nw"
        )
        self.queue_rows.bind(
            "<Configure>",
            lambda _event: self.queue_canvas.configure(
                scrollregion=self.queue_canvas.bbox("all")
            ),
        )
        self.queue_canvas.bind(
            "<Configure>",
            lambda event: self.queue_canvas.itemconfigure(
                self.queue_window, width=event.width
            ),
        )
        self.queue_canvas.bind("<MouseWheel>", self._scroll_queue)

    def _build_version_sidebar(self, shell):
        self.version_bar = tk.Frame(shell, width=300, bg="#f4e4c4")
        self.version_bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.version_bar.pack_propagate(False)

        self.version_header = tk.Canvas(
            self.version_bar, height=78, bg="#f4e4c4", highlightthickness=0
        )
        self.version_header.pack(fill=tk.X)
        self.version_header.bind("<Configure>", self._draw_version_header)

        holder = tk.Frame(self.version_bar, bg="#f4e4c4")
        holder.pack(fill=tk.BOTH, expand=True)
        self.version_canvas = tk.Canvas(holder, bg="#f4e4c4", highlightthickness=0)
        scroll = ttk.Scrollbar(
            holder, orient=tk.VERTICAL, command=self.version_canvas.yview
        )
        self.version_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.version_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.version_rows = tk.Frame(self.version_canvas, bg="#f4e4c4")
        self.version_window = self.version_canvas.create_window(
            (0, 0), window=self.version_rows, anchor="nw"
        )
        self.version_rows.bind(
            "<Configure>",
            lambda _event: self.version_canvas.configure(
                scrollregion=self.version_canvas.bbox("all")
            ),
        )
        self.version_canvas.bind(
            "<Configure>",
            lambda event: self.version_canvas.itemconfigure(
                self.version_window, width=event.width
            ),
        )
        self.version_canvas.bind("<MouseWheel>", self._scroll_versions)
        self._render_versions()

    def _scroll_versions(self, event):
        self.version_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _draw_version_header(self, _event=None):
        canvas = self.version_header
        canvas.delete("banner")
        width = max(canvas.winfo_width(), 240)
        canvas.create_rectangle(
            0, 0, width, 78, fill="#e7b56a", outline="", tags="banner"
        )
        canvas.create_text(
            width / 2,
            40,
            text="per serving",
            fill="#3a2a1a",
            font=("Segoe UI", 14, "bold"),
            tags="banner",
        )

    def _render_versions(self):
        for child in self.version_rows.winfo_children():
            child.destroy()
        bg = "#f4e4c4"
        fg = "#3a2a1a"
        if not self.last_meal or not self.versions:
            tk.Label(
                self.version_rows,
                text="Plan a meal to compare two ways of cooking it.",
                bg=bg,
                fg=fg,
                justify=tk.LEFT,
                anchor="w",
                wraplength=250,
                font=("Segoe UI", 10),
            ).pack(anchor=tk.W, padx=14, pady=14)
            return
        tk.Label(
            self.version_rows,
            text=self.last_meal,
            bg=bg,
            fg=fg,
            font=("Segoe UI", 13, "bold"),
            wraplength=250,
            justify=tk.LEFT,
            anchor="w",
        ).pack(anchor=tk.W, padx=14, pady=(12, 8))
        cards = {"tastier": "#f3d2a2", "healthier": "#d5e8b0"}
        for key in _VERSION_ORDER:
            version = self.versions.get(key)
            if version is None:
                continue
            card = tk.Frame(self.version_rows, bg=cards[key])
            card.pack(fill=tk.X, padx=14, pady=(0, 10))
            tk.Label(
                card,
                text=_VERSION_LABELS[key],
                bg=cards[key],
                fg=fg,
                font=("Segoe UI", 12, "bold"),
                anchor="w",
            ).pack(anchor=tk.W, padx=10, pady=(10, 4))
            if version.get("nutrition"):
                detail = format_nutrition(version["nutrition"])
            else:
                detail = version.get("nutrition_note") or "estimating..."
            tk.Label(
                card,
                text=detail,
                bg=cards[key],
                fg=fg,
                justify=tk.LEFT,
                anchor="w",
                font=("Segoe UI", 10),
            ).pack(anchor=tk.W, padx=10)
            selected = key == self.selected_version
            button = ttk.Button(
                card,
                text="Using this" if selected else "Use this",
                command=lambda chosen=key: self._use_version(chosen),
            )
            if selected or self.busy:
                button.configure(state=tk.DISABLED)
            button.pack(anchor=tk.W, padx=10, pady=(6, 10))
        self.version_canvas.yview_moveto(0)

    def _use_version(self, key):
        if self.busy or key not in self.versions or key == self.selected_version:
            return
        stopped = self._apply_version(key, self.current_servings)[1]
        self._render_versions()
        if stopped:
            self.set_status("list is full")
            return
        self.set_status(f"using the {_VERSION_LABELS[key].lower()} version")

    def _apply_version(self, key, servings, owned=None):
        version = self.versions[key]
        self.selected_version = key
        self.base_ingredients = list(version["ingredients"])
        self.base_method = version["method"]
        self.cook_minutes = version["minutes"]
        self._set_servings_value(servings)
        self.update_playlist_button()
        return self._place_recipe_lines(servings, owned)

    def _clear_versions(self):
        self.versions = {}
        self.selected_version = None
        self._nutrition_meal = None
        self._render_versions()

    def _estimate_nutrition(self, meal):
        self._nutrition_meal = meal
        snapshot = {
            key: list(version["ingredients"])
            for key, version in self.versions.items()
        }
        threading.Thread(
            target=self._nutrition_worker, args=(meal, snapshot), daemon=True
        ).start()

    def _nutrition_worker(self, meal, snapshot):
        results = {}
        failed = False
        try:
            for key, ingredients in snapshot.items():
                results[key] = nutrition_per_serving(ingredients)
        except (error.URLError, error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
            failed = True
        self.root.after(0, lambda: self._nutrition_ready(meal, results, failed))

    def _nutrition_ready(self, meal, results, failed):
        if meal != self._nutrition_meal or meal != self.last_meal:
            return
        for key, version in self.versions.items():
            if failed or not results.get(key):
                version["nutrition"] = None
                version["nutrition_note"] = "could not load nutrition"
            else:
                version["nutrition"] = results[key]
                version["nutrition_note"] = ""
        self._render_versions()

    def _scroll_queue(self, event):
        self.queue_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _paint_meal_header(self, _event=None):
        canvas = self.side_top
        canvas.delete("decor")
        width = max(canvas.winfo_width(), 280)
        for x, y, radius, color in (
            (36, 28, 54, "#f3c8e6"),
            (250, 46, 72, "#d48cbc"),
            (140, 160, 48, "#f6d4ec"),
            (210, 130, 28, "#c984b4"),
        ):
            canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=color, outline="", tags="decor",
            )
        if self._meal_photo is not None:
            canvas.create_image(width / 2, 96, image=self._meal_photo, tags="decor")

    def _draw_peak(self, _event=None):
        canvas = self.peak
        canvas.delete("peak")
        width = max(canvas.winfo_width(), 240)
        height = 78
        canvas.create_polygon(
            0, height,
            0, 30,
            width / 2, 0,
            width, 30,
            width, height,
            fill="#6fa33a",
            outline="",
            tags="peak",
        )
        canvas.create_text(
            width / 2,
            48,
            text="coming up next",
            fill="white",
            font=("Segoe UI", 14, "bold"),
            tags="peak",
        )

    def _show_queue(self, tracks, meal_bytes):
        from PIL import Image, ImageTk
        if meal_bytes:
            try:
                image = Image.open(io.BytesIO(meal_bytes)).convert("RGB")
                image.thumbnail((132, 132))
                self._meal_photo = ImageTk.PhotoImage(image)
                # region agent log
                _dbg("A", "notes_ui.py:_show_queue", "meal image shown", {"bytes": len(meal_bytes)})
                # endregion
            except (OSError, ValueError, tk.TclError) as exc:
                self._meal_photo = None
                # region agent log
                _dbg("A", "notes_ui.py:_show_queue", "meal image open failed", {"bytes": len(meal_bytes), "error": type(exc).__name__})
                # endregion
        else:
            self._meal_photo = None
            # region agent log
            _dbg("A", "notes_ui.py:_show_queue", "no meal image bytes", {})
            # endregion
        self._paint_meal_header()
        for child in self.queue_rows.winfo_children():
            child.destroy()
        self.queue_images = []
        for index, item in enumerate(tracks, start=1):
            row = tk.Frame(self.queue_rows, bg="#c6e58a")
            row.pack(fill=tk.X, padx=8, pady=6)
            tk.Label(
                row,
                text=f"#{index}",
                bg="#c6e58a",
                fg="#31401c",
                font=("Segoe UI", 11, "bold"),
                width=3,
            ).pack(side=tk.LEFT, anchor="n")
            cover = tk.Label(row, bg="#c6e58a")
            cover.pack(side=tk.LEFT, padx=(4, 8))
            if item.get("bytes"):
                picture = Image.open(io.BytesIO(item["bytes"])).convert("RGB")
                picture = picture.resize((48, 48))
                photo = ImageTk.PhotoImage(picture)
                self.queue_images.append(photo)
                cover.configure(image=photo)
            text = tk.Frame(row, bg="#c6e58a")
            text.pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Label(
                text,
                text=item.get("name") or "",
                bg="#c6e58a",
                fg="#1c1c1c",
                font=("Segoe UI", 10, "bold"),
                anchor="w",
                justify=tk.LEFT,
                wraplength=150,
            ).pack(fill=tk.X)
            tk.Label(
                text,
                text=item.get("artists") or "",
                bg="#c6e58a",
                fg="#3d4a22",
                font=("Segoe UI", 9),
                anchor="w",
                justify=tk.LEFT,
                wraplength=150,
            ).pack(fill=tk.X)
        self.queue_canvas.yview_moveto(0)

    def set_status(self, text):
        self.status.configure(text=text)

    def set_busy(self, busy):
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for button in self.buttons:
            if button in (self.playlist_button, self.foo_button, self.oasis_button) and not busy:
                continue
            button.configure(state=state)
        for button in self.playback_buttons:
            button.configure(state=state)
        self.entry.configure(state=state)
        if not busy:
            self.update_playlist_button()
        self._sync_servings_state()

    def update_playlist_button(self):
        if self.cook_minutes:
            self.playlist_button.configure(
                state=tk.NORMAL, text=f"Playlist ({self.cook_minutes} min)"
            )
            self.foo_button.configure(
                state=tk.NORMAL, text=f"Foo Fighters ({self.cook_minutes} min)"
            )
            self.oasis_button.configure(
                state=tk.NORMAL, text=f"Oasis ({self.cook_minutes} min)"
            )
        else:
            self.playlist_button.configure(state=tk.DISABLED, text="Playlist")
            self.foo_button.configure(state=tk.DISABLED, text="Foo Fighters")
            self.oasis_button.configure(state=tk.DISABLED, text="Oasis")

    def refresh(self):
        self.listbox.delete(0, tk.END)
        for index, note in enumerate(self.notes, start=1):
            self.listbox.insert(tk.END, f"{index}. {note}")

    def selected_index(self):
        selection = self.listbox.curselection()
        if not selection:
            return None
        return selection[0]

    def text_from_entry(self):
        return self.entry.get().strip()

    def same_as_note(self, text, skip_index=None):
        folded = " ".join(text.split()).lower()
        for index, note in enumerate(self.notes):
            if index == skip_index:
                continue
            if " ".join(note.split()).lower() == folded:
                return True
        return False

    def owned_text(self):
        return ""

    def add_note(self):
        if self.busy:
            return
        text = self.text_from_entry()
        if text == "":
            self.set_status("missing text")
            return
        text = text[:MAX_LEN]
        if self.same_as_note(text):
            self.set_status("already on the list")
            return
        if len(self.notes) == MAX_NOTES:
            self.set_status("list is full")
            return
        self.notes.append(text)
        self.refresh()
        self.set_status("saved")

    def edit_note(self):
        if self.busy:
            return
        index = self.selected_index()
        if index is None:
            self.set_status("no such note")
            return
        text = self.text_from_entry()
        if text == "":
            self.set_status("missing text")
            return
        text = text[:MAX_LEN]
        if self.same_as_note(text, skip_index=index):
            self.set_status("already on the list")
            return
        self.notes[index] = text
        self.refresh()
        self.listbox.selection_set(index)
        self.set_status("updated")

    def delete_note(self):
        if self.busy:
            return
        index = self.selected_index()
        if index is None:
            self.set_status("no such note")
            return
        del self.notes[index]
        self.refresh()
        self.set_status("deleted")

    def clear_notes(self):
        if self.busy:
            return
        self.notes.clear()
        self.last_meal = None
        self.cook_minutes = None
        self.base_ingredients = []
        self.base_method = ""
        self.recipe_notes = []
        self.current_servings = _BASE_SERVINGS
        self._set_servings_value(_BASE_SERVINGS)
        self._sync_servings_state()
        self.refresh()
        self.set_method("")
        self.update_playlist_button()
        self._clear_versions()
        self.set_status("cleared")

    def print_notes(self):
        if self.busy:
            return
        if not self.notes:
            self.set_status("(empty)")
            return
        lines = [f"Serves {self.current_servings}"]
        lines.extend(
            f"{index}. {note}" for index, note in enumerate(self.notes, start=1)
        )
        NOTES_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.set_status("printed")

    def ask_meal(self):
        if self.busy:
            return
        meal = self.text_from_entry()
        if meal == "":
            self.set_status("missing text")
            return
        if self.last_meal is not None and " ".join(meal.split()).lower() == " ".join(self.last_meal.split()).lower():
            self.set_status("already planned")
            return
        owned = owned_items(self.owned_text())
        self.set_busy(True)
        self.set_status("asking Ollama...")
        threading.Thread(
            target=self._fetch_meal, args=(meal, owned), daemon=True
        ).start()

    def set_method(self, text):
        self.method.configure(state=tk.NORMAL)
        self.method.delete("1.0", tk.END)
        if text:
            self.method.insert("1.0", text)
        self.method.configure(state=tk.DISABLED)

    def _fetch_meal(self, meal, owned):
        try:
            content = fetch_ingredients(meal)
        except (error.URLError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
            self.root.after(0, self._meal_unreachable)
            return
        versions = parse_two_meals(content)
        # region agent log
        _dbg("C", "notes_ui.py:_fetch_meal", "parsed meal", {
            "meal": meal,
            "rejected": versions is None,
            "keys": list(versions) if versions else [],
            "reply": (content or "")[:500],
        })
        # endregion
        if versions:
            for version in versions.values():
                if not ingredients_need_amounts(version["ingredients"]):
                    continue
                try:
                    measured = measure_ingredients(meal, version["ingredients"])
                except (error.URLError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
                    measured = None
                if measured and not ingredients_need_amounts(measured):
                    version["ingredients"] = [item[:MAX_LEN] for item in measured]
        self.root.after(0, lambda: self._meal_ready(meal, versions, owned))

    def _meal_unreachable(self):
        self.set_busy(False)
        self.set_status("could not reach Ollama")

    def _meal_ready(self, meal, versions, owned):
        self.set_busy(False)
        if versions is None:
            # region agent log
            _dbg("C", "notes_ui.py:_meal_ready", "rejected as not a meal", {"meal": meal})
            # endregion
            self.set_status(f'warning: "{meal}" does not fit the meal category')
            return
        if not versions:
            # region agent log
            _dbg("C", "notes_ui.py:_meal_ready", "recipe unreadable", {"meal": meal})
            # endregion
            self.set_status("could not read the recipe")
            return
        stated = minutes_from_meal_text(meal)
        if stated is not None:
            for version in versions.values():
                version["minutes"] = stated
        self.versions = versions
        self.last_meal = meal
        key = "tastier" if "tastier" in versions else next(iter(versions))
        # region agent log
        _dbg("G", "notes_ui.py:_meal_ready", "applied recipe", {
            "meal": meal,
            "minutes": versions[key]["minutes"],
            "ingredients": versions[key]["ingredients"],
            "methodStart": (versions[key]["method"] or "")[:160],
        })
        # endregion
        self.current_servings = _BASE_SERVINGS
        fresh, stopped = self._apply_version(key, _BASE_SERVINGS, owned)
        self._sync_servings_state()
        self._render_versions()
        self._estimate_nutrition(meal)
        if stopped:
            self.set_status("list is full")
            return
        if fresh:
            self.set_status("saved")
        elif self.base_ingredients:
            self.set_status("you already have the ingredients")
        else:
            self.set_status("could not get ingredients")

    def _set_servings_value(self, value):
        self._setting_servings = True
        self.servings_var.set(str(value))
        self._setting_servings = False

    def _sync_servings_state(self):
        if self.busy or not (self.base_ingredients or self.base_method):
            self.servings_box.configure(state=tk.DISABLED)
        else:
            self.servings_box.configure(state=tk.NORMAL)

    def _notes_without_recipe(self):
        pending = list(self.recipe_notes)
        kept = []
        for note in self.notes:
            if note in pending:
                pending.remove(note)
                continue
            kept.append(note)
        return kept

    def _recipe_lines_for(self, servings, owned=None):
        factor = servings / _BASE_SERVINGS
        if owned is None:
            owned = owned_items(self.owned_text())
        kept = self._notes_without_recipe()
        fresh = []
        for ingredient in self.base_ingredients:
            text = scale_ingredient(ingredient, factor)[:MAX_LEN]
            if already_have(text, owned) or text in kept or text in fresh:
                continue
            fresh.append(text)
        return kept, fresh, factor

    def _place_recipe_lines(self, servings, owned=None):
        kept, fresh, factor = self._recipe_lines_for(servings, owned)
        stopped = False
        if len(kept) + len(fresh) > MAX_NOTES:
            fresh = fresh[:MAX_NOTES - len(kept)]
            stopped = True
        self.notes = kept + fresh
        self.recipe_notes = list(fresh)
        self.current_servings = servings
        self.set_method(scale_method(self.base_method, factor))
        self.refresh()
        return fresh, stopped

    def _rescale_recipe(self, servings):
        if ingredients_need_amounts(self.base_ingredients):
            meal = self.last_meal or ""
            ingredients = list(self.base_ingredients)
            self.set_busy(True)
            self.set_status("adding amounts...")
            threading.Thread(
                target=self._measure_worker,
                args=(meal, ingredients, servings),
                daemon=True,
            ).start()
            return
        kept, fresh, factor = self._recipe_lines_for(servings)
        if len(kept) + len(fresh) > MAX_NOTES:
            self._set_servings_value(self.current_servings)
            self.set_status("list is full")
            return
        self.notes = kept + fresh
        self.recipe_notes = list(fresh)
        self.current_servings = servings
        self.set_method(scale_method(self.base_method, factor))
        self.refresh()
        self.set_status(f"scaled to {servings} servings")

    def _measure_worker(self, meal, ingredients, servings):
        try:
            measured = measure_ingredients(meal, ingredients)
        except (error.URLError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
            self.root.after(0, lambda: self._measure_failed())
            return
        self.root.after(0, lambda m=measured: self._measure_ready(m, servings))

    def _measure_failed(self):
        self.set_busy(False)
        self._set_servings_value(self.current_servings)
        self.set_status("could not reach Ollama")

    def _measure_ready(self, measured, servings):
        self.set_busy(False)
        if not measured or ingredients_need_amounts(measured):
            self._set_servings_value(self.current_servings)
            self.set_status("could not add amounts")
            return
        self.base_ingredients = [item[:MAX_LEN] for item in measured]
        version = self.versions.get(self.selected_version)
        if version is not None:
            version["ingredients"] = list(self.base_ingredients)
            version["nutrition"] = None
            version["nutrition_note"] = "estimating..."
            self._render_versions()
            if self.last_meal:
                self._estimate_nutrition(self.last_meal)
        self._rescale_recipe(servings)

    def _servings_changed(self, _event=None):
        if self._setting_servings or self.busy:
            return
        if not (self.base_ingredients or self.base_method):
            return
        try:
            servings = int(self.servings_var.get().strip())
        except (ValueError, tk.TclError):
            self._set_servings_value(self.current_servings)
            return
        if servings < 1 or servings > 12:
            self._set_servings_value(self.current_servings)
            return
        if servings == self.current_servings:
            return
        self._rescale_recipe(servings)

    def ask_playlist(self):
        self._start_playlist(create_cooking_playlist)

    def ask_foo_playlist(self):
        self._start_playlist(create_foo_fighters_playlist)

    def ask_oasis_playlist(self):
        self._start_playlist(create_oasis_playlist)

    def _start_playlist(self, builder):
        if self.busy or not self.cook_minutes or not self.last_meal:
            return
        meal = self.last_meal
        minutes = self.cook_minutes
        photo = self._meal_photo_bytes
        self.set_busy(True)
        self.set_status("building playlist...")
        threading.Thread(
            target=self._build_playlist, args=(builder, meal, minutes, photo), daemon=True
        ).start()

    def _build_playlist(self, builder, meal, minutes, photo):
        playlist_id = None
        try:
            name, playlist_id = builder(meal, minutes, photo)
            try:
                token = spotify_access_token()
                spotify_play(
                    token,
                    f"spotify:playlist:{playlist_id}",
                    spotify_device_id(token),
                )
                status = f"playing: {name}"
            except RuntimeError as exc:
                status = f"playlist saved: {name}. {exc}"
            except (error.URLError, error.HTTPError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
                status = f"playlist saved: {name}. could not start playback"
        except RuntimeError as exc:
            status = str(exc)
        except (error.URLError, error.HTTPError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
            status = "could not build playlist"
        self.root.after(0, lambda: self._playlist_done(status, playlist_id))

    def _playlist_done(self, status, playlist_id):
        self.set_busy(False)
        if playlist_id:
            self.last_playlist_id = playlist_id
        self.set_status(status)
        if status.startswith("playing"):
            self.root.after(500, self._poll_now_once)

    def _run_playback(self, action):
        if self.busy:
            return
        self.set_busy(True)
        self.set_status("controlling Spotify...")
        threading.Thread(target=self._playback_worker, args=(action,), daemon=True).start()

    def _playback_worker(self, action):
        step = "start"
        try:
            step = "token"
            token = spotify_access_token()
            step = "devices"
            device_id = spotify_device_id(token)
            step = action
            if action == "play":
                player = spotify_playback(token)
                has_track = bool(player and player.get("item"))
                if has_track:
                    spotify_play(token, None, device_id)
                elif self.last_playlist_id:
                    spotify_play(
                        token,
                        f"spotify:playlist:{self.last_playlist_id}",
                        device_id,
                    )
                else:
                    spotify_play(token, None, device_id)
                status = "playing"
            elif action == "pause":
                spotify_pause(token, device_id)
                status = "paused"
            elif action == "next":
                spotify_next(token, device_id)
                status = "skipped"
            elif action == "shuffle":
                player = spotify_playback(token)
                enabled = not bool(player and player.get("shuffle_state"))
                spotify_shuffle(token, enabled, device_id)
                status = "shuffle on" if enabled else "shuffle off"
            else:
                spotify_previous(token, device_id)
                status = "previous track"
            # region agent log
            _agent_log("E", "notes_ui.py:_playback_worker", "control succeeded", {"action": action, "status": status, "step": step})
            # endregion
        except RuntimeError as exc:
            status = str(exc)
            # region agent log
            _agent_log("C", "notes_ui.py:_playback_worker", "runtime error", {"action": action, "step": step, "error": str(exc)})
            # endregion
        except error.HTTPError as exc:
            detail = ""
            try:
                body = json.loads(exc.read().decode("utf-8", errors="replace"))
                detail = body.get("error", {}).get("message", "")
            except (OSError, json.JSONDecodeError, AttributeError):
                detail = ""
            status = detail or "could not control Spotify"
            # region agent log
            _agent_log("B", "notes_ui.py:_playback_worker", "http error became status", {"action": action, "step": step, "code": exc.code, "detail": detail, "status": status})
            # endregion
        except (error.URLError, TimeoutError, OSError, KeyError, json.JSONDecodeError) as exc:
            status = "could not control Spotify"
            # region agent log
            _agent_log("A", "notes_ui.py:_playback_worker", "non-http error became status", {"action": action, "step": step, "errorType": type(exc).__name__, "error": str(exc)[:200]})
            # endregion
        self.root.after(0, lambda: self._playback_done(status))

    def _set_shuffle(self, enabled):
        self.shuffle_on = enabled
        if enabled:
            self.shuffle_button.configure(text="Shuffle on")
        elif enabled is False:
            self.shuffle_button.configure(text="Shuffle off")
        else:
            self.shuffle_button.configure(text="Shuffle")

    def _playback_done(self, status):
        self.set_busy(False)
        self.set_status(status)
        if status == "shuffle on":
            self._set_shuffle(True)
        elif status == "shuffle off":
            self._set_shuffle(False)
        if status in ("playing", "paused", "skipped", "previous track", "shuffle on", "shuffle off"):
            self.root.after(800, self._poll_now_once)

    def _poll_now_once(self):
        if self._polling or not TOKEN_FILE.exists():
            return
        if not os.environ.get("SPOTIFY_CLIENT_ID", "").strip():
            return
        self._polling = True
        threading.Thread(target=self._load_now_playing, daemon=True).start()

    def playback_play(self):
        self._run_playback("play")

    def playback_pause(self):
        self._run_playback("pause")

    def playback_next(self):
        self._run_playback("next")

    def playback_previous(self):
        self._run_playback("previous")

    def playback_shuffle(self):
        self._run_playback("shuffle")

    def refresh_now_playing(self):
        self.root.after(5000, self.refresh_now_playing)
        if self._polling or not TOKEN_FILE.exists():
            return
        if not os.environ.get("SPOTIFY_CLIENT_ID", "").strip():
            return
        self._polling = True
        threading.Thread(target=self._load_now_playing, daemon=True).start()

    def _load_now_playing(self):
        track = None
        image_bytes = None
        shuffle_on = None
        queue = []
        meal_key = meal_lookup_name(self.last_meal or "")
        fetch_meal = bool(meal_key) and meal_key != self._meal_photo_key
        meal_bytes = None
        try:
            token = spotify_access_token()
            player = spotify_playback(token)
            if isinstance(player, dict):
                shuffle_on = bool(player.get("shuffle_state"))
            track = current_track(token)
            if track and track["image"]:
                image_bytes = _read_url(track["image"])
        except (error.URLError, error.HTTPError, TimeoutError, OSError, RuntimeError, KeyError, json.JSONDecodeError):
            track = None
        try:
            token = spotify_access_token()
            for item in upcoming_tracks(token):
                cover = None
                if item["image"]:
                    try:
                        cover = _read_url(item["image"])
                    except (error.URLError, error.HTTPError, TimeoutError, OSError):
                        cover = None
                item["bytes"] = cover
                queue.append(item)
        except (error.URLError, error.HTTPError, TimeoutError, OSError, RuntimeError, KeyError, json.JSONDecodeError):
            queue = []
        if fetch_meal:
            try:
                meal_bytes = meal_photo_bytes(self.last_meal)
            except (error.URLError, error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
                meal_bytes = None
        self.root.after(
            0, lambda t=track, b=image_bytes, s=shuffle_on: self._show_now(t, b, s)
        )
        self.root.after(
            0,
            lambda q=queue, m=meal_bytes, k=meal_key, fetch=fetch_meal: self._apply_queue(q, m, k, fetch),
        )

    def _apply_queue(self, tracks, meal_bytes, meal_key, fetch_meal):
        if fetch_meal:
            self._meal_photo_key = meal_key
            self._meal_photo_bytes = meal_bytes
        elif not meal_key:
            self._meal_photo_key = None
            self._meal_photo_bytes = None
            meal_bytes = None
        else:
            meal_bytes = self._meal_photo_bytes
        self._show_queue(tracks, meal_bytes)

    def _show_now(self, track, image_bytes, shuffle_on=None):
        self._polling = False
        if shuffle_on is not None:
            self._set_shuffle(shuffle_on)
        if not track:
            self.now_text.configure(text="Nothing playing")
            self.cover.configure(image="")
            self.cover_image = None
            return
        self.now_text.configure(text=f"{track['name']}\n{track['artists']}")
        if not image_bytes:
            return
        from PIL import Image, ImageTk
        image = Image.open(io.BytesIO(image_bytes)).resize((64, 64))
        self.cover_image = ImageTk.PhotoImage(image)
        self.cover.configure(image=self.cover_image)


def main():
    root = tk.Tk()
    MealPlannerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
