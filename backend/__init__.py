"""Backend package for the web-view version of the MC3 Music Manager.

The whole point of this port: the UI moves to HTML/CSS/JS, but the domain
logic (PS2 tool orchestration, ffmpeg, ISO handling, backup) stays Python and
is reused. This package holds that Python side + the bridge to the web UI.
"""
