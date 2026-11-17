# Demo project for the Kavach scanner bot

A deliberately outdated little web shop. The scanner bot scans this folder (mounted at /scan), reports what it finds
in the chat, and fixes it only after you confirm. Ask the chat to "reset the demo project" to start again.

Planted problems: old dependencies with known CVEs (requirements.txt, package.json), a hard-coded database password
(config.py), debug mode on (app.py), a secrets file readable by everyone (.env) that is not in .gitignore, and a
Dockerfile that uses the `latest` tag and runs as root.
