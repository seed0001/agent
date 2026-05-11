import sqlite3
from pathlib import Path

p = Path(r'C:\Users\aztre\Desktop\Andrew-Core-Foundation\data\profiles\default\memory.db')
print('exists', p.exists(), 'size', p.stat().st_size if p.exists() else 0)
con = sqlite3.connect(str(p))
cur = con.cursor()
print('\nTABLES')
for r in cur.execute("select name from sqlite_master where type='table' order by name"):
    print('-', r[0])

for table in ['profile_facts','episodic_memory','working_memory','sessions','background_thoughts']:
    try:
        c = cur.execute(f'select count(*) from {table}').fetchone()[0]
        print(table, c)
    except Exception as e:
        print(table, 'ERR', e)

print('\nPROFILE_FACTS')
try:
    rows = cur.execute('select category,value,confidence,protected,source from profile_facts where deleted_at is null order by confidence desc limit 120').fetchall()
    for row in rows:
        print(row)
except Exception as e:
    print('facts err', e)

print('\nWORKING_MEMORY')
try:
    for row in cur.execute('select key,value,updated_at from working_memory order by updated_at desc limit 80'):
        print(row)
except Exception as e:
    print('working err', e)

print('\nRECENT_EPISODIC')
try:
    rows = cur.execute('select role,content,importance,created_at from episodic_memory order by id desc limit 120').fetchall()
    for role, content, importance, created_at in rows:
        print('---', role, created_at, importance)
        print((content or '')[:1200].replace('\n',' '))
except Exception as e:
    print('episodic err', e)
