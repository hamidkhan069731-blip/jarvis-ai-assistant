"""Local (offline) provider intent routing.

The local brain is a real natural-language intent router — not a simulated LLM.
These tests lock down the command mappings the spec calls out, including the
multilingual cases (English, Roman Urdu verb-first *and* verb-final phrasing).
"""
from __future__ import annotations

import pytest

from jarvis.ai.local_provider import LocalProvider
from jarvis.ai.provider import ToolCall


@pytest.fixture
def provider() -> LocalProvider:
    return LocalProvider()


def _route(provider: LocalProvider, text: str):
    return provider._route(text)


def _tool(provider: LocalProvider, text: str) -> ToolCall:
    out = _route(provider, text)
    assert isinstance(out, ToolCall), f"expected a tool call for {text!r}, got text"
    return out


# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,tool_name", [
    ("what time is it", "get_time"),
    ("what's my CPU", "system_status"),
    ("system info", "system_info"),
    ("take a screenshot", "take_screenshot"),
    ("lock the computer", "lock_workstation"),
    ("open youtube", "open_url"),
    ("open chrome", "open_application"),
    ("search the web for python tutorials", "web_search"),
    ("organize my downloads", "organize_folder"),
    ("run command dir", "run_shell"),
    ("note that buy milk", "add_note"),
    ("list my notes", "list_notes"),
])
def test_routes_to_expected_tool(provider, text, tool_name):
    assert _tool(provider, text).name == tool_name


def test_calculator_routes_bare_expression(provider):
    tc = _tool(provider, "2+2*3")
    assert tc.name == "calculate"
    assert tc.arguments["expression"] == "2+2*3"


def test_open_folder_alias_resolves(provider):
    tc = _tool(provider, "open downloads")
    assert tc.name == "open_path"
    assert "Downloads" in tc.arguments["path"]


def test_shell_precedes_open_rule(provider):
    # "run command ..." must map to the shell, not open_application("command ...")
    tc = _tool(provider, "run command echo hi")
    assert tc.name == "run_shell"
    assert tc.arguments["command"] == "echo hi"


# --- multilingual: Roman Urdu ---------------------------------------------- #
def test_roman_urdu_verb_final_open(provider):
    tc = _tool(provider, "chrome kholo")
    assert tc.name == "open_application"
    assert tc.arguments["name"] == "chrome"


def test_roman_urdu_verb_final_close(provider):
    tc = _tool(provider, "notepad band karo")
    assert tc.name == "close_application"
    assert tc.arguments["name"] == "notepad"


def test_roman_urdu_folder_open(provider):
    tc = _tool(provider, "downloads kholo")
    assert tc.name == "open_path"
    assert "Downloads" in tc.arguments["path"]


def test_roman_urdu_organize(provider):
    tc = _tool(provider, "downloads organize karo")
    assert tc.name == "organize_folder"


# --- non-tool conversational replies --------------------------------------- #
def test_greeting_is_plain_text(provider):
    out = _route(provider, "hello")
    assert isinstance(out, str)
    assert "JARVIS" in out or "J.A.R.V.I.S" in out


def test_help_lists_capabilities(provider):
    out = _route(provider, "what can you do")
    assert isinstance(out, str)
    assert "System" in out


def test_unmatched_input_gives_helpful_fallback(provider):
    out = _route(provider, "compose a symphony in D minor")
    assert isinstance(out, str)
    assert "didn't match" in out or "cloud mode" in out


def test_tool_result_message_is_summarised(provider):
    # when the last message is a tool result, the provider echoes its summary
    resp = provider.complete("sys", [{"role": "tool", "summary": "It is 5 PM.",
                                      "content": "It is 5 PM."}], [])
    assert resp.text == "It is 5 PM."
    assert not resp.wants_tools


# --------------------------------------------------------------------------- #
# PC power routing (Phase 3)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,action", [
    ("shut down the computer", "shutdown"),
    ("shutdown", "shutdown"),
    ("restart the pc", "restart"),
    ("reboot", "restart"),
    ("hibernate the laptop", "hibernate"),
    ("sign out", "sign_out"),
    ("log off", "sign_out"),
    ("put the computer to sleep", "sleep"),
    ("sleep the pc", "sleep"),
])
def test_power_commands_route_to_power_control(provider, text, action):
    tc = _tool(provider, text)
    assert tc.name == "power_control"
    assert tc.arguments["action"] == action


def test_restart_app_is_not_a_pc_restart(provider):
    # "restart chrome" is ambiguous-verb + no device word -> must NOT power off.
    out = _route(provider, "restart chrome")
    assert not (isinstance(out, ToolCall) and out.name == "power_control")


def test_lock_still_routes_to_lock_not_power(provider):
    assert _tool(provider, "lock the computer").name == "lock_workstation"


# --------------------------------------------------------------------------- #
# Window / app control routing (Phase 3)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,tool_name", [
    ("what's open", "list_windows"),
    ("list my open windows", "list_windows"),
    ("switch to chrome", "focus_window"),
    ("focus on vs code", "focus_window"),
    ("minimize chrome", "set_window_state"),
    ("maximize notepad", "set_window_state"),
    ("is chrome running", "is_app_running"),
    ("restart chrome", "restart_application"),
    ("press ctrl+s", "press_keys"),
])
def test_window_commands_route_correctly(provider, text, tool_name):
    assert _tool(provider, text).name == tool_name


def test_restart_app_targets_the_app(provider):
    tc = _tool(provider, "restart chrome")
    assert tc.name == "restart_application"
    assert tc.arguments["name"] == "chrome"


def test_pc_restart_still_beats_app_restart(provider):
    # The power rules run first, so a device word keeps this a power action.
    for phrase in ["restart the computer", "restart the pc", "restart my laptop"]:
        tc = _tool(provider, phrase)
        assert tc.name == "power_control", phrase
        assert tc.arguments["action"] == "restart"


def test_window_state_picks_the_right_state(provider):
    assert _tool(provider, "minimize chrome").arguments["state"] == "minimize"
    assert _tool(provider, "maximize chrome").arguments["state"] == "maximize"


def test_switch_to_does_not_launch_a_duplicate(provider):
    tc = _tool(provider, "switch to chrome")
    assert tc.name == "focus_window"
    assert tc.arguments["query"] == "chrome"


def test_open_still_launches(provider):
    # "open chrome" must remain a launch, not a focus.
    assert _tool(provider, "open chrome").name == "open_application"


# --------------------------------------------------------------------------- #
# Screen understanding routing (Phase 4)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,tool_name", [
    ("read the screen", "read_screen"),
    ("what's on my screen", "read_screen"),
    ("what does the screen say", "read_screen"),
    ("describe the screen", "describe_screen"),
    ("what am I looking at", "describe_screen"),
])
def test_screen_commands_route_correctly(provider, text, tool_name):
    assert _tool(provider, text).name == tool_name


def test_screenshot_still_captures_rather_than_reading(provider):
    # "take a screenshot" must stay a capture, not become an OCR read.
    assert _tool(provider, "take a screenshot").name == "take_screenshot"


def test_find_on_screen_extracts_the_needle(provider):
    tc = _tool(provider, "find Submit on screen")
    assert tc.name == "find_on_screen"
    # The router normalizes to lowercase; the search itself is case-insensitive.
    assert tc.arguments["text"].lower() == "submit"


def test_read_window_targets_that_window(provider):
    tc = _tool(provider, "read the chrome window")
    assert tc.name == "read_screen"
    assert tc.arguments["window"] == "chrome"

