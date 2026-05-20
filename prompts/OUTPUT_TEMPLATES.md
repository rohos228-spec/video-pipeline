# GPT output templates for mass generation

These templates define the output shapes that scripts can parse without manual cleanup.

## Step 1: plan

Return an updated `project.xlsx` file. The file is the source of truth.

## Step 2: voiceover script

Return one `.txt` file containing only the final voiceover text. No markdown, headings, analysis, or character-count footer.

## Step 3: split into frames

Return an updated `project.xlsx` file with frame blocks written into the expected frame cells.

Fallback plain-text format, only when requested:

block 1
-
block 2
-
block 3

## Step 4: hero prompt

Return only the finished English image prompt. No markdown, quotes, or explanation. Maximum length: 5000 characters.

## Step 5: enrich xlsx

Return an updated `project.xlsx` file. Preserve workbook structure unless the instruction explicitly requires a change.

## Step 6: image prompts

Return plain text prompts in frame order. Separate prompts with a line containing exactly `-`.

## Step 8: animation prompts

For one frame, return only the final animation prompt.

For file-name mapping mode:

FILE: <image_file_name>
PROMPT: <animation_prompt>

## Review prompts

Return strict JSON only:

{
  "decision": "approved",
  "confidence": 0.0,
  "criteria": {},
  "issues": [],
  "fix_hints": []
}
