"""Magpie as a desktop app: always running, always listening for “notes on”.

The app runs the Magpie server inside itself (no Docker, no password), listens to the
microphone with an offline speech model (Vosk), and when it hears the command word it
shows a small bar over whatever you're doing with your words as you speak. Reminders
pop up in the corner of the screen. It starts with the computer, lives in the system
tray, and updates itself.

Everything that touches the screen, the microphone or the operating system is imported
lazily, so the logic here can be tested anywhere.
"""
