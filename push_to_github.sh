#!/bin/bash
# Uploads this project to your PRIVATE GitHub repository and writes the submission PDF.
#   1. On github.com: New repository -> name it (for example kavach-assist-24BTRCL114) -> PRIVATE -> Create (no README).
#   2. bash push_to_github.sh https://github.com/<your-user>/<repo>.git
cd "$(dirname "$0")" || exit 1
URL="$1"
case "$URL" in https://github.com/*/*) ;; *) echo "usage: bash push_to_github.sh https://github.com/<your-user>/<repo>.git"; exit 1;; esac

if git ls-files --error-unmatch .env >/dev/null 2>&1; then echo "STOP: .env is tracked by git - it holds your passwords. Tell Claude."; exit 1; fi
[ -d .git ] || git init -q
git branch -M main
git add -A
if git diff --cached --name-only | grep -qx ".env"; then echo "STOP: .env would be uploaded. Tell Claude."; exit 1; fi
NAME=$(git config user.name || echo "M Nagasai Girisharan"); EMAIL=$(git config user.email || echo "24BTRCL114@users.noreply.github.com")
git -c user.name="$NAME" -c user.email="$EMAIL" commit -q \
    -m "Final assessment: Kavach Assist - LLM-backed chat application on MongoDB (login, RAG, memory, scanner bot)" \
    -m "Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HRMHP5D7yxJzJjSAAmgrah" || echo "(nothing new to commit)"
git remote remove origin 2>/dev/null
git remote add origin "$URL"
echo "== pushing to $URL (GitHub asks you to sign in the first time)"
git push -u origin main || { echo; echo "PUSH FAILED. Easiest fix: install GitHub Desktop, sign in, 'Add existing repository' -> this folder -> Publish."; exit 1; }

echo "== writing the submission PDF"
docker exec lab7 python labs/final/make_submission.py "$URL" && echo "PDF: results/Final_Assessment_Submission.pdf  <- upload this file to Google Classroom"
echo
echo "Last step on github.com: your repository -> Settings -> Collaborators -> Add people -> your faculty's GitHub username."
