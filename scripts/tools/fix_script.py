import json
import sqlite3

with open('/tmp/s01e024_script.json', 'r') as f:
    script = json.load(f)

for scene in script['scenes']:
    prompt = scene['visual_prompt']
    
    # Revert the overly strict hair prompt
    prompt = prompt.replace("pure pitch-black hair (hex #000000, absolutely no blue/grey tint)", "black hair")
    
    scene['visual_prompt'] = prompt

new_script_json = json.dumps(script, ensure_ascii=False)

conn = sqlite3.connect('/Users/mac/project/interactive_video_pipeline/storage/pipeline.db')
cursor = conn.cursor()
cursor.execute(
    "UPDATE episodes SET script_json=?, status='GENERATING_ASSETS', error_stage=NULL, error_message=NULL WHERE id=26",
    (new_script_json,)
)
conn.commit()
conn.close()

print("Database updated, relaxed the strict hair prompt!")
