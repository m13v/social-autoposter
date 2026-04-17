#!/usr/bin/env python3
"""Log Fazm LinkedIn comments batch 2 to DB."""
from dotenv import load_dotenv
load_dotenv()
import psycopg2, os

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

posts = [
    # (platform, thread_url, thread_author, thread_author_handle, thread_title, thread_content_summary, our_url, our_content, our_account, source_summary)
    ('linkedin', 'https://www.linkedin.com/in/rizwan-rizwan-1351a650/', 'Rizwan Rizwan', 'rizwan-rizwan-1351a650',
     'Ghost OS - AI agent that controls your Mac',
     'Open sourced AI agent using macOS accessibility tree for desktop control',
     'https://www.linkedin.com/in/rizwan-rizwan-1351a650/',
     'the accessibility tree approach is such a game changer vs screenshot-based agents. I\'ve been building something similar for macOS and once you tap into the native accessibility APIs you get the full UI structure instantly',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/rogerglovsky/', 'Roger Glovsky', 'rogerglovsky',
     'GPT-5.4 Native Desktop Agent - computer use via screenshots',
     'GPT-5.4 native computer-use capabilities using screenshot-based coordinate clicking',
     'https://www.linkedin.com/in/rogerglovsky/',
     'the screenshot + coordinate clicking approach works but it\'s surprisingly brittle in practice. been working on a macOS agent that reads the native accessibility tree instead',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/bilaldalgun/en/', 'Bilal Dalgun', 'bilaldalgun',
     'OpenClaw browser automation for procurement',
     'Open-source AI agent for browser automation in procurement workflows',
     'https://www.linkedin.com/in/bilaldalgun/en/',
     'the browser automation part is where these agents still struggle the most though. clicking through supplier portals by taking screenshots and guessing coordinates breaks constantly',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/amr-abdeldaym/', 'Amr Abdeldaym', 'amr-abdeldaym',
     'Perplexity Personal Computer - desktop AI automation',
     'AI agents working locally on desktop handling complex tasks autonomously',
     'https://www.linkedin.com/in/amr-abdeldaym/',
     'the local execution angle is what makes this interesting. the big gap right now is how these agents actually interact with desktop apps',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/thomas-ulleberg-53b6588/', 'Thomas Ulleberg', 'thomas-ulleberg-53b6588',
     'AI agents running tasks overnight on separate machine',
     'Building persistent AI sessions that control browsers, desktop apps, and system tools',
     'https://www.linkedin.com/in/thomas-ulleberg-53b6588/',
     'the 3am screen hijacking story is so relatable. most frameworks just screenshot the screen and throw it at a vision model. switching to native accessibility APIs changed everything',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/huzaifa-tariq-719b72307/', 'Huzaifa Tariq', 'huzaifa-tariq-719b72307',
     'GPT-5.4 Pro computer use with pixel-perfect screenshots',
     'GPT-5.4 Pro native computer use via high-fidelity screenshots',
     'https://www.linkedin.com/in/huzaifa-tariq-719b72307/',
     'impressive benchmarks but there\'s a cost angle nobody talks about with the screenshot approach. on macOS there\'s actually a much lighter path - the OS exposes a full accessibility tree',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/luke-cathcart-70b1a861/', 'Luke Cathcart', 'luke-cathcart-70b1a861',
     'AI-Native Operating System replacing SaaS dashboards',
     'AI agents orchestrating tools and processes as the new interface',
     'https://www.linkedin.com/in/luke-cathcart-70b1a861/',
     'totally agree about AI becoming the orchestration layer. the missing piece right now is how agents actually interact with all those native apps',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/baroness-nicola-tennant-7097a2220/', 'Baroness Nicola Tennant', 'baroness-nicola-tennant-7097a2220',
     'Desktop AI agent with full native app control',
     'Windows + Mac automation software with true desktop control beyond browser agents',
     'https://www.linkedin.com/in/baroness-nicola-tennant-7097a2220/',
     'this is the right direction - real desktop control is so much more useful than browser-only agents. I went the accessibility API route for macOS',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/apoorv-sarawgi-589521395/', 'Apoorv Sarawgi', 'apoorv-sarawgi-589521395',
     'GPT-5.4 Direct Computer Use as Operator',
     'GPT-5.4 transition from chatbot to operator with direct computer use',
     'https://www.linkedin.com/in/apoorv-sarawgi-589521395/',
     'the "Second Brain with hands" framing is spot on but the Direct Computer Use implementation still relies on screenshot parsing. on macOS you can skip all of that by reading the accessibility tree',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    # Also log the 3 unlogged posts from earlier session
    ('linkedin', 'https://www.linkedin.com/company/blackops-studio/posts/', 'BlackOps Studio', 'blackops-studio',
     'Mac mini AI server with OpenClaw',
     'Mac mini as 24/7 AI system with OpenClaw for personal agents',
     'https://www.linkedin.com/company/blackops-studio/posts/',
     'commented on accessibility APIs vs screenshots for desktop agents',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/ghemid-mohamed/', 'Ilias Charizanis', 'ilias-charizanis',
     'OpenClaw digital worker',
     'OpenClaw as autonomous digital worker for desktop tasks',
     'https://www.linkedin.com/in/ghemid-mohamed/',
     'commented on accessibility APIs approach for desktop agents',
     'Matthew Diakonov', 'fazm linkedin engagement'),

    ('linkedin', 'https://www.linkedin.com/in/dimosthenis-spyridis/', 'Dimosthenis Spyridis', 'dimosthenis-spyridis',
     'Perplexity vs OpenClaw productization',
     'Comparison of Perplexity Computer vs OpenClaw approaches',
     'https://www.linkedin.com/in/dimosthenis-spyridis/',
     'commented on accessibility APIs vs screenshot approach',
     'Matthew Diakonov', 'fazm linkedin engagement'),
]

for p in posts:
    cur.execute("""
        INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
            thread_title, thread_content, our_url, our_content, our_account,
            source_summary, status, posted_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', NOW())
    """, p)

conn.commit()
print(f"Logged {len(posts)} posts to DB")
cur.execute("SELECT id, thread_author FROM posts WHERE source_summary='fazm linkedin engagement' ORDER BY id DESC LIMIT 15")
for r in cur.fetchall():
    print(r)
conn.close()
