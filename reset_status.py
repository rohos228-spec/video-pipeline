import sqlite3
c = sqlite3.connect("data/state.db")
c.execute("UPDATE projects SET status='frames_ready', hero_description=NULL WHERE id=1")
c.execute("DELETE FROM artifacts WHERE frame_id IS NULL AND kind='scene_image'")
c.commit()
print("ok")
