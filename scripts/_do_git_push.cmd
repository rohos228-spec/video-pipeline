@echo off
cd /d "C:\Users\Love Space\video-pipeline"
echo === GIT PUSH === > _git_push_log.txt
git rev-parse --show-toplevel >> _git_push_log.txt 2>&1
git status -sb >> _git_push_log.txt 2>&1
git diff --stat >> _git_push_log.txt 2>&1
git log -3 --oneline >> _git_push_log.txt 2>&1
git branch -vv >> _git_push_log.txt 2>&1
git remote -v >> _git_push_log.txt 2>&1
git add -A >> _git_push_log.txt 2>&1
git commit -m "Add GPT Image 2 quality and 1K resolution options with screenshot defaults" >> _git_push_log.txt 2>&1
git rev-parse HEAD >> _git_push_log.txt 2>&1
git push -u origin HEAD >> _git_push_log.txt 2>&1
if errorlevel 1 (
  echo PUSH FAILED, REBASE >> _git_push_log.txt
  git pull --rebase origin HEAD >> _git_push_log.txt 2>&1
  git push -u origin HEAD >> _git_push_log.txt 2>&1
)
echo DONE exit=%errorlevel% >> _git_push_log.txt
