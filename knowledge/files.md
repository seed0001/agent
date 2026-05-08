# File Operations

These tools were rebuilt in May 2026 after I kept reporting "saved!" on writes the user couldn't find. The fix below is what changed and how I should use the tools now.

## What changed

- Every file tool now resolves the path I pass to an **absolute** path before doing anything.
- `write_file` is **atomic**: it writes to a sibling temp file, fsyncs, then `os.replace()` onto the destination. A crash mid-write leaves the destination either fully old or fully new — never partial.
- `write_file` **verifies** after writing: it stats the destination and confirms the byte count matches what I sent. If it doesn't match, I get `Error: ... did not verify`, not a false success.
- All success and error messages **echo the absolute path**. When I tell the user where a file lives, I quote what `write_file` returned to me — never the relative name I passed in.
- A new `verify_file_exists` tool lets me confirm a file is really there before I claim it is.
- The core now has two reliability guardrails around my output:
  - **Tool invocation recovery** normalizes safe near-miss tool names (`Write_file`, `write file`, `write_file.`) and recovers explicit text-form calls like `Save to C:\path\file.txt: content` into the real structured tool call.
  - **File-claim verification** checks my final reply. If I say I saved/created/wrote a file without same-turn `write_file` or `verify_file_exists` evidence, the reply is corrected before it reaches Travis.

---

## write_file

**When to use:** Create or overwrite a file. Save content. Write config.

**How:**
- `path`: where to write. Relative paths resolve against my current working directory; the success message tells me the absolute path that resulted.
- `content`: full content (string) to write. Empty string is a valid empty file. `None` is rejected as a programming error.
- Parent directories are created automatically.
- Returns `Written and verified: <ABSOLUTE_PATH> (<N> bytes)` on success.
- Returns `Error: ...` with the resolved absolute path and the specific failure (permission, disk full, target is a directory, parent missing, etc.) on failure.

**Critical rule:** when I tell the user a file was saved, I quote the absolute path from the success message. Never the relative path I passed in.

**Even more critical:** I do not claim a file was saved just because I wrote words that sound like a tool call. Plain text such as `write_file`, `Write_file`, `write file`, or `Save to ...` is only recoverable when the core parser catches it and actually runs the real tool. Success still requires `Written and verified: ...`.

**Examples:**
```
write_file("notes.txt", "User notes")
  → Written and verified: C:\Users\aztre\Desktop\agent\notes.txt (10 bytes)
  ✅ Tell the user: "Saved to C:\Users\aztre\Desktop\agent\notes.txt (10 bytes)"
  ❌ Don't say:    "Saved to notes.txt"

write_file("projects/schedule.txt", "...")
  → Written and verified: C:\Users\aztre\Desktop\agent\projects\schedule.txt (412 bytes)
  ✅ Tell the user: "Saved to C:\Users\aztre\Desktop\agent\projects\schedule.txt"
```

**Tips:**
- For appending, read first, concatenate, write back.
- If a write fails, the destination is untouched — I can safely retry without worrying about a half-written file.
- I cannot accidentally overwrite a directory; I'll get `Error: refusing to overwrite directory at ...`.

---

## verify_file_exists

**When to use:** Whenever I've just written a file and I want to be SURE before telling the user. Cheap to call — use it freely.

**How:**
- `path`: file to check. Resolves to absolute.
- Returns `EXISTS: <ABSOLUTE_PATH> (<N> bytes)`, `DIRECTORY: <ABSOLUTE_PATH> (<N> entries)`, or `NOT FOUND: <ABSOLUTE_PATH>`.

**Recommended pattern when saving anything important for the user:**
```
1. write_file("projects/schedule.txt", body)
   → "Written and verified: C:\Users\aztre\Desktop\agent\projects\schedule.txt (412 bytes)"
2. verify_file_exists("projects/schedule.txt")
   → "EXISTS: C:\Users\aztre\Desktop\agent\projects\schedule.txt (412 bytes)"
3. NOW I can tell the user: "Schedule saved to C:\Users\aztre\Desktop\agent\projects\schedule.txt"
```

If verify_file_exists comes back NOT FOUND or with a smaller byte count, I do NOT tell the user it's saved. I report the failure and try again.

---

## Tool invocation recovery

The preferred path is still a real structured function call with the exact registered tool name and JSON arguments.

If I fail that and output a safe near-miss, the core attempts recovery:

```
Write_file     → write_file
write file     → write_file
write_file.    → write_file
check file exists → verify_file_exists
open file      → read_file
run shell      → run_command
```

The core can also recover explicit text-form calls:

```
Save to C:\Users\aztre\Desktop\agent\andrew's projects\note.txt: hello
  → write_file(path="C:\Users\aztre\Desktop\agent\andrew's projects\note.txt", content="hello")

Check if file exists at C:\Users\aztre\Desktop\agent\andrew's projects\note.txt
  → verify_file_exists(path="C:\Users\aztre\Desktop\agent\andrew's projects\note.txt")
```

This is a backup, not permission to be sloppy. The recovery layer does **not** trust narrative claims like "I've saved the file." Those still need real tool evidence.

---

## read_file

**When to use:** Read file contents.

**How:**
- `path`: full or relative. Supports `~` for home.
- Returns the file contents on success.
- On failure returns `Error: File not found at <ABSOLUTE_PATH> (you passed: '<RAW>')` so I can see exactly where I looked. If the user thinks the file should be elsewhere, that absolute path is what to compare against.

**Tips:**
- Use `list_dir` first if I'm not sure of the path.
- The "(you passed: ...)" trailer in the error message is the exact string I was working from — useful when the user and I disagree on what path I "meant".

---

## list_dir

**When to use:** Explore folders, find files, see project structure, locate a file.

**How:**
- `path`: optional. Defaults to my current working directory.
- Output **starts with `# <ABSOLUTE_PATH>`** as the first line so I know exactly where I'm looking.
- Subdirectories are shown with a trailing `/`.
- Empty directories return `(empty)` after the header.

**Examples:**
```
list_dir("projects")
  → # C:\Users\aztre\Desktop\agent\projects
  →   schedule.txt
  →   notes/
```
