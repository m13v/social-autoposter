import psycopg2, os
from dotenv import load_dotenv
load_dotenv()

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

posts = [
    {
        'thread_url': 'https://www.linkedin.com/feed/update/urn:li:activity:7305628143879385088/',
        'thread_author': 'Jeff Keltner',
        'thread_author_handle': 'jeff-keltner',
        'thread_title': 'GPT 5.4 native computer use',
        'thread_content': 'Post about GPT 5.4 native computer use and the shift from assistant to agent',
        'our_url': 'https://www.linkedin.com/feed/update/urn:li:activity:7305628143879385088/',
        'our_content': 'the "assistant to agent" shift is the right frame. what\'s interesting is that screenshot-based approaches (which most computer use models rely on) hit a ceiling fast on complex UIs - pixel matching breaks when windows overlap, menus change, or the app updates. we\'ve been building a macOS agent that uses native accessibility APIs instead, which gives it the actual UI tree rather than guessing from screenshots. the accuracy difference on multi-step workflows is significant, and the token cost drops because you\'re passing structured data instead of images.',
    },
    {
        'thread_url': 'https://www.linkedin.com/in/keval-parmar234/',
        'thread_author': 'Keval Parmar',
        'thread_author_handle': 'keval-parmar234',
        'thread_title': 'Optimus V3 desktop AI assistant',
        'thread_content': 'Post about building Optimus V3 desktop AI assistant with voice control, LangGraph routing, HuggingFace LLMs',
        'our_url': 'https://www.linkedin.com/in/keval-parmar234/',
        'our_content': "cool to see another builder going down the desktop AI agent path. we've been working on something similar for macOS, biggest lesson was ditching screenshot-based interaction and using native accessibility APIs instead - you get the actual UI tree so the agent doesn't have to guess what's on screen. the accuracy jump on multi-step workflows was massive. curious if you've hit the same wall with visual approaches on Optimus?",
    },
    {
        'thread_url': 'https://www.linkedin.com/in/gongdao/',
        'thread_author': 'GongDao',
        'thread_author_handle': 'gongdao',
        'thread_title': 'OpenClaw criticism - macOS AI agent alternatives',
        'thread_content': 'Post criticizing OpenClaw screenshot-based approach and suggesting Shortcuts/AppleScript instead',
        'our_url': 'https://www.linkedin.com/in/gongdao/',
        'our_content': "you're right that the screenshot-upload-control loop is wasteful. but Shortcuts and AppleScript only cover a fraction of what apps expose. there's a middle path - macOS accessibility APIs give you the full UI tree of any app natively, no screenshots needed. i've been building a desktop agent on top of this and the token cost drops dramatically because you're passing structured element data instead of images. plus it works reliably even when windows overlap or menus change.",
    },
    {
        'thread_url': 'https://www.linkedin.com/in/jfstovall/',
        'thread_author': 'James Stovall',
        'thread_author_handle': 'jfstovall',
        'thread_title': 'Claude Cowork and OpenClaw security concerns',
        'thread_content': 'Post about security risks of computer-use agents, burner laptops, screenshot data exposure',
        'our_url': 'https://www.linkedin.com/in/jfstovall/',
        'our_content': 'the security angle is exactly why i moved away from screenshot-based computer use for my own agent work. every screenshot gets uploaded to a remote API - that\'s your entire screen, passwords, emails, everything visible. i\'ve been building on macOS accessibility APIs instead, which gives the agent the UI element tree directly. no screenshots leaving the machine, no pixel data going to cloud inference. the agent sees "button: Submit Order" not a 1920x1080 image of your desktop. solves a huge chunk of the data exposure problem while actually being more accurate for multi-step tasks.',
    },
]

for p in posts:
    cur.execute("""INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
        thread_title, thread_content, our_url, our_content, our_account,
        source_summary, status, posted_at)
    VALUES ('linkedin', %s, %s, %s, %s, %s, %s, %s, 'm13v',
        'fazm - macOS desktop AI agent, accessibility APIs vs screenshots', 'active', NOW())
    RETURNING id""",
    (p['thread_url'], p['thread_author'], p['thread_author_handle'],
     p['thread_title'], p['thread_content'], p['our_url'], p['our_content']))
    row = cur.fetchone()
    print(f"Inserted post id={row[0]}: {p['thread_title'][:50]}")

conn.commit()
cur.close()
conn.close()
print("Done - all 4 LinkedIn Fazm comments logged")
