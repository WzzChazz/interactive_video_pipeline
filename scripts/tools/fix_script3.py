import json
import sqlite3

with open('/tmp/s01e024_script.json', 'r') as f:
    script = json.load(f)

for scene in script['scenes']:
    prompt = scene['visual_prompt']
    
    # Prepend extreme darkness instruction to enforce environment, but DO NOT delete the props!
    prompt = "A dimly lit, extremely dark and spooky abandoned archive room at midnight. Pitch black background. Only a single faint flashlight beam illuminating the character from below. " + prompt
    
    # Only fix the impossible contradiction in scene 6 (white tiles background in pure darkness)
    prompt = prompt.replace("sterile white tiles background in pure darkness", "pure darkness")
    prompt = prompt.replace("sterile white tiles background", "pure darkness")
    
    scene['visual_prompt'] = prompt

new_script_json = json.dumps(script, ensure_ascii=False)

conn = sqlite3.connect('/Users/mac/project/interactive_video_pipeline/storage/pipeline.db')
cursor = conn.cursor()
cursor.execute(
    "UPDATE episodes SET script_json=?, status='GENERATING_ASSETS', asset_manifest_json=NULL, error_stage=NULL, error_message=NULL WHERE id=26",
    (new_script_json,)
)
conn.commit()
conn.close()

print("Database updated with exact pristine props + extreme darkness wrapper!")
