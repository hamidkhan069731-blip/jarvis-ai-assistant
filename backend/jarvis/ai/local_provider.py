"""Offline, rule-based provider — the deterministic command router.

This is NOT a simulated LLM. It is a genuine natural-language intent router
that maps spoken/typed commands to real tool calls, so JARVIS is fully useful
with no API key and no internet. It understands English, Roman Urdu, and mixed
phrasing (e.g. "Chrome kholo", "meri downloads organize karo").

Cloud providers add open-ended reasoning and multi-step planning; this local
brain gives fast, predictable single-command execution.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional, Union

from jarvis.ai.provider import AIProvider, AIResponse, ToolCall

_counter = {"n": 0}


def _tid() -> str:
    _counter["n"] += 1
    return f"local_{_counter['n']}"


def _call(tool_name: str, **args) -> ToolCall:
    return ToolCall(id=_tid(), name=tool_name, arguments=args)


# Common folder aliases -> absolute path string
_FOLDERS = {
    "downloads": "Downloads", "download": "Downloads",
    "desktop": "Desktop",
    "documents": "Documents", "docs": "Documents",
    "pictures": "Pictures", "photos": "Pictures", "images": "Pictures",
    "music": "Music", "videos": "Videos", "movies": "Videos",
}

_SITES = {
    "youtube": "https://youtube.com", "google": "https://google.com",
    "gmail": "https://mail.google.com", "github": "https://github.com",
    "twitter": "https://twitter.com", "x": "https://x.com",
    "facebook": "https://facebook.com", "reddit": "https://reddit.com",
    "wikipedia": "https://wikipedia.org", "chatgpt": "https://chat.openai.com",
    "stackoverflow": "https://stackoverflow.com", "linkedin": "https://linkedin.com",
}

_EXT_WORDS = {
    "pdf": "pdf", "pdfs": "pdf", "word": "docx", "excel": "xlsx", "image": "png",
    "images": "png", "photo": "jpg", "photos": "jpg", "video": "mp4", "videos": "mp4",
    "text": "txt", "python": "py", "music": "mp3", "song": "mp3", "songs": "mp3",
}


def _folder_path(word: str) -> Optional[str]:
    key = word.strip().lower()
    if key in _FOLDERS:
        return str(Path.home() / _FOLDERS[key])
    return None


# --------------------------------------------------------------------------- #
class LocalProvider(AIProvider):
    name = "local"
    mode = "local"

    def available(self) -> bool:
        return True

    # -- main entry ----------------------------------------------------- #
    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> AIResponse:
        # If the most recent message is a tool result, produce the final answer
        # from the tool's own human-readable summary.
        if messages and messages[-1]["role"] == "tool":
            summary = messages[-1].get("summary") or messages[-1].get("content", "")
            return AIResponse(text=summary, provider=self.name, mode=self.mode)

        user_text = ""
        for m in reversed(messages):
            if m["role"] == "user":
                user_text = m["content"] if isinstance(m["content"], str) else str(m["content"])
                break

        result = self._route(user_text.strip())
        if isinstance(result, ToolCall):
            return AIResponse(tool_calls=[result], provider=self.name, mode=self.mode,
                              finish_reason="tool_calls")
        return AIResponse(text=result, provider=self.name, mode=self.mode)

    # -- routing -------------------------------------------------------- #
    def _route(self, text: str) -> Union[ToolCall, str]:
        low = text.lower().strip()
        if not low:
            return "I'm listening. What would you like me to do?"

        for pattern, handler in self._RULES:
            m = re.search(pattern, low)
            if m:
                out = handler(self, m, text)
                if out is not None:
                    return out
        return self._fallback(text)

    # -- individual handlers ------------------------------------------- #
    def _h_greeting(self, m, text) -> str:
        return ("Good to see you. J.A.R.V.I.S. online and ready. "
                "You can ask me to open apps, manage files, check the system, "
                "search the web, and more. Say 'what can you do' for the full list.")

    def _h_help(self, m, text) -> str:
        return (
            "Here's what I can do right now, sir:\n\n"
            "**System** — 'what's my CPU/RAM/disk', 'system info', 'what's using memory', 'lock the computer'\n"
            "**Apps** — 'open Chrome', 'launch VS Code', 'close Notepad', 'open youtube'\n"
            "**Files** — 'find my PDFs', 'organize my Downloads', 'largest files', 'duplicate images', 'create a folder called X'\n"
            "**Web** — 'search the web for ...', 'look up ...'\n"
            "**Volume** — 'volume up/down/mute', 'take a screenshot'\n"
            "**Memory** — 'remember that ...', 'what do you remember about ...'\n\n"
            "Anything risky (deleting, moving, running commands) I'll always confirm first."
        )

    def _h_time(self, m, text): return _call("get_time")
    def _h_status(self, m, text): return _call("system_status")
    def _h_sysinfo(self, m, text): return _call("system_info")
    def _h_processes(self, m, text):
        by = "cpu" if re.search(r"cpu|processor", text.lower()) else "memory"
        return _call("list_processes", sort_by=by, limit=8)

    def _h_calc(self, m, text):
        expr = m.group("expr") if "expr" in m.groupdict() else text
        expr = re.sub(r"[^0-9+\-*/%.() ]", "", expr).strip()
        if not expr:
            return None
        return _call("calculate", expression=expr)

    def _h_volume(self, m, text):
        low = text.lower()
        if "mute" in low or "unmute" in low or "silent" in low:
            action = "mute"
        elif re.search(r"up|increase|louder|zyada|barha", low):
            action = "up"
        else:
            action = "down"
        return _call("control_volume", action=action)

    def _h_lock(self, m, text): return _call("lock_workstation")
    def _h_screenshot(self, m, text): return _call("take_screenshot")

    def _h_power(self, m, text):
        low = text.lower()
        if re.search(r"\b(restart|reboot)\b", low):
            action = "restart"
        elif re.search(r"\b(shut\s?down|power\s?off|turn\s+off)\b", low):
            action = "shutdown"
        elif re.search(r"\bhibernate\b", low):
            action = "hibernate"
        elif re.search(r"\b(sign\s?out|log\s?off|log\s?out)\b", low):
            action = "sign_out"
        elif re.search(r"\b(sleep|suspend)\b", low):
            action = "sleep"
        else:
            return None
        return _call("power_control", action=action)

    def _h_clipboard_get(self, m, text): return _call("clipboard_get")

    def _h_open(self, m, text):
        target = m.group("target").strip().strip(".!?")
        key = target.lower()
        if key in _SITES:
            return _call("open_url", url=_SITES[key])
        folder = _folder_path(key)
        if folder:
            return _call("open_path", path=folder)
        if re.match(r"^(https?://|www\.)|\.[a-z]{2,}($|/)", key):
            return _call("open_url", url=target)
        return _call("open_application", name=target)

    def _h_close(self, m, text):
        target = m.group("target").strip().strip(".!?")
        return _call("close_application", name=target)

    def _h_list_windows(self, m, text): return _call("list_windows")

    def _h_focus_window(self, m, text):
        target = m.group("target").strip().strip(".!?")
        return _call("focus_window", query=target) if target else None

    def _h_window_state(self, m, text):
        low = text.lower()
        if re.search(r"\bmaximi[sz]e\b", low):
            state = "maximize"
        elif re.search(r"\brestore\b|\bun-?minimi[sz]e\b", low):
            state = "restore"
        else:
            state = "minimize"
        target = m.group("target").strip().strip(".!?")
        return _call("set_window_state", query=target, state=state) if target else None

    def _h_is_running(self, m, text):
        target = m.group("target").strip().strip(".!?")
        return _call("is_app_running", name=target) if target else None

    def _h_restart_app(self, m, text):
        target = m.group("target").strip().strip(".!?")
        return _call("restart_application", name=target) if target else None

    def _h_press_keys(self, m, text):
        combo = m.group("combo").strip().strip(".!?")
        return _call("press_keys", combo=combo) if combo else None

    def _h_read_screen(self, m, text):
        win = (m.groupdict().get("target") or "").strip().strip(".!?")
        return _call("read_screen", window=win) if win else _call("read_screen")

    def _h_find_on_screen(self, m, text):
        needle = m.group("needle").strip().strip(".!?\"'")
        return _call("find_on_screen", text=needle) if needle else None

    def _h_describe_screen(self, m, text): return _call("describe_screen")

    def _h_type_text(self, m, text):
        value = m.group("value").strip()
        return _call("type_text", text=value) if value else None

    def _h_search_web(self, m, text):
        query = m.group("query").strip()
        return _call("web_search", query=query) if query else None

    def _h_find_files(self, m, text):
        low = text.lower()
        # extension?
        ext = ""
        for word, e in _EXT_WORDS.items():
            if re.search(rf"\b{word}\b", low):
                ext = e
                break
        # explicit name after 'named' / 'called'
        name_m = re.search(r"(?:named|called|name)\s+([\w.-]+)", low)
        query = name_m.group(1) if name_m else ""
        # which folder?
        path = "."
        for word in _FOLDERS:
            if re.search(rf"\b{word}\b", low):
                path = _folder_path(word) or "."
                break
        return _call("search_files", query=query, extension=ext, path=path, limit=100)

    def _h_large_files(self, m, text):
        path = self._pick_folder(text)
        return _call("find_large_files", path=path, min_mb=100)

    def _h_duplicates(self, m, text):
        path = self._pick_folder(text)
        return _call("find_duplicate_files", path=path)

    def _h_organize(self, m, text):
        path = self._pick_folder(text, default="Downloads")
        return _call("organize_folder", path=path)

    def _h_list_dir(self, m, text):
        path = self._pick_folder(text, default="")
        return _call("list_directory", path=path or ".")

    def _h_create_folder(self, m, text):
        name = m.group("name").strip().strip("'\"")
        return _call("create_folder", path=name) if name else None

    def _h_remember(self, m, text):
        value = m.group("value").strip()
        return _call("remember", kind="fact", value=value) if value else None

    def _h_recall(self, m, text):
        q = m.group("query").strip()
        return _call("recall", query=q) if q else None

    def _h_add_note(self, m, text):
        value = m.group("value").strip()
        return _call("add_note", text=value) if value else None

    def _h_list_notes(self, m, text):
        return _call("list_notes", limit=10)

    def _h_delete(self, m, text):
        target = m.group("target").strip().strip(".!?")
        return _call("delete_path", path=target) if target else None

    def _h_shell(self, m, text):
        cmd = m.group("cmd").strip()
        return _call("run_shell", command=cmd) if cmd else None

    # -- helpers -------------------------------------------------------- #
    def _pick_folder(self, text: str, default: str = "") -> str:
        low = text.lower()
        for word in _FOLDERS:
            if re.search(rf"\b{word}\b", low):
                return _folder_path(word) or "."
        if default:
            return str(Path.home() / default)
        return "."

    def _fallback(self, text: str) -> str:
        return (
            "I can act on that best in cloud mode, but locally I didn't match a "
            "command. Try things like: 'open Chrome', 'what's my CPU', "
            "'find my PDFs', 'organize my Downloads', or 'search the web for ...'. "
            "Say 'what can you do' for the full list."
        )

    # Ordered rules: first match wins. Roman-Urdu keywords included.
    _RULES: list[tuple[str, Callable]] = [
        # Greetings only when they LEAD the utterance, so "find hi-res images" or
        # "run command echo hi" aren't hijacked by a stray "hi"/"hey" mid-sentence.
        (r"^\s*(hello|hi|hey|yo|hiya|salaam|assalam(?:u alaikum)?|good (morning|evening|afternoon))\b", _h_greeting),
        (r"\bhey jarvis\b", _h_greeting),
        (r"\b(what can you do|help|capabilities|commands|kya kar sakte)\b", _h_help),
        (r"\b(what('| i)?s the |current )?(time|date)\b|kya time|waqt", _h_time),
        (r"\b(cpu|ram|memory|disk|battery|system status|how('| i)?s my (pc|computer|system)|performance)\b", _h_status),
        (r"\b(system info|specs|specifications|what (pc|computer|processor))\b", _h_sysinfo),
        (r"\b(processes|what('| i)?s using|top process|task manager list)\b", _h_processes),
        # PC power. Unambiguous verbs fire directly; ambiguous ones (restart /
        # sleep / turn off) require a device word so "restart chrome" stays an
        # app action, and "go to sleep" (agent standby, handled in core) is not
        # confused with "put the computer to sleep".
        (r"\b(shut\s?down|power\s?off|reboot|hibernate|sign\s?out|log\s?off|log\s?out)\b", _h_power),
        (r"\b(restart|turn\s+off|sleep|suspend)\b.*\b(pc|computer|laptop|system|machine|windows)\b", _h_power),
        (r"\b(pc|computer|laptop|system|machine|windows)\b.*\b(restart|turn\s+off|sleep|suspend|shut\s?down)\b", _h_power),
        (r"\b(volume|sound|awaz|mute|unmute)\b", _h_volume),
        (r"\b(lock (the )?(computer|pc|screen|workstation)|lock it)\b|computer lock", _h_lock),
        # Screen understanding. These precede the screenshot rule so "read the
        # screen" performs OCR, while "take a screenshot" still just captures.
        (r"\bread\s+(?:the\s+)?(?P<target>[\w .-]+?)\s+window\b", _h_read_screen),
        (r"\b(?:read|scan)\s+(?:the\s+|my\s+)?screen\b|\bwhat(?:'| i)?s on (?:the|my) screen\b|\bwhat does (?:the|my) screen say\b", _h_read_screen),
        (r"\b(?:find|locate)\s+[\"']?(?P<needle>[^\"']+?)[\"']?\s+on\s+(?:the\s+|my\s+)?screen\b", _h_find_on_screen),
        (r"\b(?:describe (?:the |my )?screen|what am i looking at|what do you see)\b", _h_describe_screen),
        (r"\b(screenshot|screen shot|capture (the )?screen)\b", _h_screenshot),
        (r"\b(what('| i)?s (in|on) (the )?clipboard|read clipboard)\b", _h_clipboard_get),
        # Window management. These sit after the power rules (so "restart the
        # computer" stays a power action) but before the generic open/close
        # rules, so "switch to chrome" focuses rather than launching a copy.
        (r"\b(what('| i)?s open|which (windows|apps) are open|list (my )?(open )?windows|show (my )?(open )?windows|open windows)\b", _h_list_windows),
        (r"\b(?:switch to|focus(?: on)?|bring up|bring me)\s+(?P<target>[\w .-]+)", _h_focus_window),
        (r"\b(?:minimi[sz]e|maximi[sz]e|restore)\s+(?P<target>[\w .-]+)", _h_window_state),
        (r"\bis\s+(?P<target>[\w .-]+?)\s+(?:running|open|still running)\b", _h_is_running),
        (r"\brestart\s+(?P<target>[\w .-]+)", _h_restart_app),
        (r"\b(?:press|hit)\s+(?P<combo>[a-z0-9]+(?:\s*\+\s*[a-z0-9]+)*)\b", _h_press_keys),
        (r"\btype\s+(?P<value>.+)", _h_type_text),
        (r"\b(calculate|what is|what'?s|compute|solve)\b.*?(?P<expr>[-\d][-\d+*/%.() ]{2,})", _h_calc),
        (r"(?P<expr>^[-\d][-\d+*/%.() ]{2,}$)", _h_calc),
        (r"\b(run command|execute|shell)\s+(?P<cmd>.+)", _h_shell),
        # Roman-Urdu verb-final phrasing (verb comes AFTER the target):
        #   "chrome kholo", "notepad band karo", "downloads khol do"
        (r"\b(?P<target>[\w .:/\\-]+?)\s+khol\s*(?:o|do|na|iye|ein|dein)?\b", _h_open),
        (r"\b(?P<target>[\w .:/\\-]+?)\s+band\s+kar\s*(?:o|do|na|dein|diye)?\b", _h_close),
        # English / verb-first phrasing:
        (r"\b(?:open|launch|start|run|khol(?:o|do)?)\s+(?P<target>[\w .:/\\-]+)", _h_open),
        (r"\b(?:close|quit|exit|kill|band kar(?:o|do)?)\s+(?P<target>[\w .-]+)", _h_close),
        (r"\b(?:search (?:the )?web for|google|look up|search for|web search)\s+(?P<query>.+)", _h_search_web),
        (r"\b(organi[sz]e|clean up|tidy)\b.*\b(folder|downloads|desktop|documents|files)\b|organi[sz]e|clean up", _h_organize),
        (r"\b(largest|biggest|large) files?\b|eating (my )?disk|disk space", _h_large_files),
        (r"\b(duplicate|duplicates|repeated) (files?|images?|photos?)\b", _h_duplicates),
        (r"\b(find|search|locate|dhoond)\b.*\b(file|files|pdf|pdfs|document|photo|image|video|song)", _h_find_files),
        (r"\b(list|show)\b.*\b(files|folder|directory|contents)\b", _h_list_dir),
        (r"\b(note that|add a note|take a note|jot down|note down)\s+(?P<value>.+)", _h_add_note),
        (r"\b(list|show|read)( my)? notes\b", _h_list_notes),
        (r"\b(create|make|new)\s+(?:a\s+)?(?:folder|directory)\s+(?:called\s+|named\s+)?(?P<name>[\w .-]+)", _h_create_folder),
        (r"\bremember (that )?(?P<value>.+)", _h_remember),
        (r"\b(what do you remember|recall|do you remember)\b.*?(?P<query>[\w .-]+)?$", _h_recall),
        (r"\b(delete|remove|trash)\s+(?P<target>[\w .:/\\-]+)", _h_delete),
    ]
