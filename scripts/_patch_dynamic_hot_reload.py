from pathlib import Path

p = Path(r'C:\Users\aztre\Desktop\Andrew-Core-Foundation\src\agent\core.py')
text = p.read_text(encoding='utf-8', errors='ignore')
old = '''        elif name in self._dynamic_runners:
            runner = self._dynamic_runners[name]
            result = await runner(**{k: v for k, v in args.items() if v is not None})
        else:
            result = f"Unknown tool: {name}"
'''
new = '''        elif name in self._dynamic_runners:
            runner = self._dynamic_runners[name]
            result = await runner(**{k: v for k, v in args.items() if v is not None})
        else:
            # Hot-reload dynamic tools once before giving up. This restores
            # live-session tool creation: a tool written to src/tools/dynamic/
            # can be used immediately on the next attempted call.
            self._reload_dynamic()
            if name in self._dynamic_runners:
                runner = self._dynamic_runners[name]
                result = await runner(**{k: v for k, v in args.items() if v is not None})
            else:
                result = f"Unknown tool: {name}"
'''
if old not in text:
    raise SystemExit('target block not found')
p.write_text(text.replace(old, new), encoding='utf-8')
print('patched dynamic hot reload')
