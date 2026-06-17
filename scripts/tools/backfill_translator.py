import json
from loguru import logger
from database.db_session import get_session
from database.models import Episode
import openai
from config.settings import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

TRANSLATE_PROMPT = """You are a professional translator specializing in psychological horror thrillers.
Translate the following Chinese script excerpts into English.
Return ONLY a valid JSON object matching this exact schema:
{{
  "scenes": [
    {{"scene_index": 1, "english_dialogue": "..."}},
    {{"scene_index": 2, "english_dialogue": "..."}}
  ],
  "english_branch_a_teaser": "...",
  "english_branch_b_teaser": "..."
}}

Here is the Chinese source:
{source_text}

CRITICAL: Return ONLY JSON. Do NOT wrap in ```json ```. Do NOT add any preamble.
"""

def backfill_translations():
    logger.info("Starting legacy backfill translation for episodes 14-16...")
    client = openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    
    with get_session() as session:
        episodes = session.query(Episode).filter(Episode.episode_number.in_([14, 15, 16])).order_by(Episode.episode_number).all()
        for ep in episodes:
            if not ep.script_json:
                continue
            
            script_data = json.loads(ep.script_json)
            logger.info(f"Processing Episode {ep.episode_number}...")
            
            # Extract source text
            source_data = {
                "scenes": [
                    {"scene_index": s["scene_index"], "dialogue": s.get("dialogue", "")}
                    for s in script_data.get("scenes", [])
                ],
                "branch_a_teaser": script_data.get("next_branches", {}).get("branch_a_teaser", ""),
                "branch_b_teaser": script_data.get("next_branches", {}).get("branch_b_teaser", ""),
            }
            source_text = json.dumps(source_data, ensure_ascii=False, indent=2)
            prompt = TRANSLATE_PROMPT.format(source_text=source_text)
            
            # Direct LLM call
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            raw_response = response.choices[0].message.content or ""
            
            # Clean Markdown if exists
            if raw_response.startswith("```"):
                raw_response = raw_response.split("```")[1]
                if raw_response.startswith("json"):
                    raw_response = raw_response[4:]
            raw_response = raw_response.strip(" \n\r\t`")
            
            try:
                translated_data = json.loads(raw_response)
                # Inject translations back
                scene_dict = {s["scene_index"]: s.get("english_dialogue", "") for s in translated_data.get("scenes", [])}
                for s in script_data.get("scenes", []):
                    idx = s["scene_index"]
                    s["english_dialogue"] = scene_dict.get(idx, "")
                
                script_data["next_branches"]["english_branch_a_teaser"] = translated_data.get("english_branch_a_teaser", "Option A")
                script_data["next_branches"]["english_branch_b_teaser"] = translated_data.get("english_branch_b_teaser", "Option B")
                
                # Save to DB
                ep.script_json = json.dumps(script_data, ensure_ascii=False, indent=2)
                session.commit()
                logger.success(f"Episode {ep.episode_number} translated and saved.")
            except Exception as e:
                logger.error(f"Failed to parse or save translation for Episode {ep.episode_number}: {e}\nRaw Response: {raw_response}")
                session.rollback()
                
    logger.success("Backfill translation completed!")

if __name__ == "__main__":
    backfill_translations()
