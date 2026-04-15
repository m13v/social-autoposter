# Reddit Pipeline Analysis & Recommendations

Generated: 2026-04-13

## Current State

- **2,732 comments** tracked with upvotes, **3,821 reply candidates**, **931 DMs sent**
- **Schedule**: every 30 min, up to 100 comments per run, ~137/day average (last 7 days)
- **Median upvotes**: 1 | **P90**: 4 | **Average**: 2.96 | **Max**: 639
- **Distribution**: heavily right-skewed; 58.6% of all posts sit at exactly 1 upvote (default self-upvote)
- **Account**: Deep_Ad1959 (2,602 posts), plus minor activity under u/Deep_Ad1959 (126) and m13v (4)

---

## Top Performing Comments

| Upvotes | Content | Subreddit | Project | Length |
|---------|---------|-----------|---------|--------|
| 639 | "880+ days in, relationship to discomfort changed, not that feelings went away" | r/selfimprovement | Vipassana | 77 chars |
| 220 | Restaurant side of DoorDash cancellation nightmare, skeleton crew late night chaos | r/doordash | PieLine | 434 chars |
| 185 | Testing never gets rewarded because test creation friction is too high | r/ExperiencedDevs | Assrt | 594 chars |
| 167 | Reverse engineering Disney Infinity binary, call graph tracing workflow | r/ClaudeAI | (none) | 117 chars |
| 115 | Leaning into what AI is bad at | r/webdev | (none) | 44 chars |
| 104 | "My career bet turned out to be writing specs. I run 5 claude agents in parallel..." | r/ClaudeAI | (none) | 293 chars |
| 94 | "AI doesn't remove bottlenecks, it moves them downstream" | r/coding | Fazm | 240 chars |
| 92 | Extension fingerprinting plus canvas/webgl creates unique identity | r/overemployed | AI Browser Profile | 340 chars |
| 85 | Personal vipassana retreat story: suffering was reaction to pain, not pain itself | r/Mindfulness | (none) | 796 chars |
| 65 | WidgetKit + SwiftData sync difficulty, congrats on 2k downloads | r/ClaudeCode | (none) | 355 chars |

## Worst Performing Comments

| Upvotes | Content | Subreddit | Project | Failure Pattern |
|---------|---------|-----------|---------|-----------------|
| -22 | "opus 4.6 genuinely good at UI work... pocketmux is cool idea" | r/ClaudeCode | Fazm | Veiled product endorsement |
| -19 | CC advantage over Cursor in codebase interaction | r/cursor | Fazm | Product comparison in hostile sub |
| -13 | Questioning port-kill tool about PID race conditions | r/node | Assrt | curious_probe in hostile sub |
| -12 | Sysco buying Restaurant Depot, lock in pricing advice | r/smallbusiness | PieLine | Unsolicited business advice |
| -9 | Explaining why restaurants use surcharges instead of updating prices | r/EndTipping | PieLine | Wrong audience for this take |
| -8 | Self-reply with link to fazm.ai | r/devops | Fazm | Blatant self-promotion |
| -7 | Missed phone calls during peak hours, audit missed call logs | r/restaurantowners | PieLine | Reads like software pitch |
| -7 | Labor costs kill operators; phone is most undervalued revenue channel | r/texas | PieLine | Off-topic for the sub |
| -6 | CC skills vs Cursor skills comparison | r/cursor | (none) | Product evangelism |
| -6 | AI camera monitoring suggestion after burglary post | r/sanfrancisco | Cyrano | Tone-deaf product suggestion |

---

## Deep Content Pattern Analysis

### 1. Comment Length: Short Dominates

| Length Bucket | Count | Avg Upvotes | Max |
|---------------|-------|-------------|-----|
| Short (<100 chars) | 194 | **6.66** | 639 |
| Medium (100-300) | 502 | 2.95 | 167 |
| Long (300-600) | 1,747 | 2.64 | 220 |
| Very Long (600+) | 289 | 2.37 | 85 |

### 2. Sentence Count: Bimodal Distribution

| Sentences | Count | Avg Upvotes | Max |
|-----------|-------|-------------|-----|
| 1 sentence | 258 | **6.03** | 639 |
| 2 sentences | 234 | 1.90 | 41 |
| 3 sentences | 598 | 2.50 | 94 |
| 4-5 sentences | 1,276 | 2.88 | 220 |
| 6+ sentences | 366 | 2.46 | 85 |

**Key insight**: Go bimodal. Either write ONE punchy sentence (6.03 avg) or commit to 4-5 sentences of real substance (2.88 avg). The 2-3 sentence range is a dead zone: too long to be punchy, too short to be substantive.

### 3. Product Name Mentions Kill Performance

| Category | Count | Avg Upvotes | Median | Max |
|----------|-------|-------------|--------|-----|
| Mentions product name | 139 | **1.17** | 1 | 10 |
| No product mention | 2,593 | **3.05** | 1 | 639 |

Product names in comments cap upside at 10. Never mention fazm, assrt, pieline, cyrano, terminator, mk0r, or s4l by name.

### 4. Links Destroy Performance

| Category | Count | Avg Upvotes | Median |
|----------|-------|-------------|--------|
| Has link (.com, .ai, .io, http) | 103 | **1.38** | 1 |
| No link | 2,629 | **3.02** | 1 |

### 5. Comment Opening Patterns

| Opening Type | Count | Avg Upvotes | Max |
|-------------|-------|-------------|-----|
| First person ("I", "my") | 77 | **4.18** | 104 |
| You/Your opening | 18 | **4.11** | 39 |
| Other opening | 1,635 | 3.04 | 639 |
| "The" opening | 837 | 2.71 | 185 |
| "This" opening | 165 | 2.63 | 59 |

First person openings are **massively underutilized** (only 2.8% of comments) despite being the highest performing category. "I built...", "I've been...", "My experience..." should be the default.

### 6. Questions vs Statements

| Type | Count | Avg Upvotes | Max |
|------|-------|-------------|-----|
| Statement only | 2,563 | **2.98** | 639 |
| Has question mark | 169 | 2.64 | 85 |

Pure statements outperform. Reddit rewards authoritative contributions, not "anyone else experience this?"

---

## Reply Target Analysis

### OP Replies vs Commenter Replies

| Target | Count | Avg Upvotes | Max |
|--------|-------|-------------|-----|
| Reply to OP | 121 | **10.82** | 639 |
| Reply to commenter | 2,611 | 2.59 | 220 |

**Replying to OP gets 4.2x the upvotes.** Currently only 4.4% of comments reply to OP. This is the single biggest structural opportunity.

### First Comment in Subreddit Per Day

| Position | Count | Avg Upvotes | Max |
|----------|-------|-------------|-----|
| First in sub that day | 1,188 | **3.87** | 639 |
| 2nd-3rd in sub | 660 | 1.98 | 47 |
| 4th+ in sub | 884 | 2.45 | 167 |

First comment in each subreddit each day is your best shot. Diminishing returns after that.

### Multiple Comments Per Thread

Multi-comment threads average 1.0-1.5 upvotes per comment. **One comment per thread, always.**

---

## Engagement Pipeline Performance

### Reply Pipeline (3,821 candidates)
- **36.5% reply rate** at depth 1 (1,226 replies out of 3,358 candidates)
- Skip reasons are sensible: filtered_author (282), not_directed_at_us (196), banned_subreddit (160), too_short (255)
- Deep engagement drops steeply: 48 at depth 2, 9 at depth 3

### Impact of Engagement on Post Performance

| Category | Posts | Avg Upvotes | Median | Link Edit Rate |
|----------|-------|-------------|--------|----------------|
| Got engagement replies | 728 | **5.58** | 2 | 27.6% |
| No replies received | 2,004 | 2.00 | 1 | 13.4% |

Posts that receive engagement replies get **2.8x the upvotes** (partly selection bias, but pipeline correctly doubles down on winners).

### Link Edit Pipeline
- **470 posts link-edited** (17.2% of total)
- Edited posts average **11.72 upvotes** vs 1.10 for below-threshold
- Only 4 eligible posts missed. Pipeline is highly effective.

### DM Pipeline
- **931 DMs** total, 773 sent (83%), 119 skipped, 38 errors
- **31.3% response rate** on cold DMs (excellent)
- 199 conversations went 3+ messages deep
- 14 flagged for human takeover, but only 6 human replies actually sent
- **Human escalation is the bottleneck**: high-value leads (Discord/Telegram requests, demo offers) going cold

---

## Timing Analysis

### Day of Week

| Day | Count | Avg Upvotes | Max |
|-----|-------|-------------|-----|
| **Sunday** | 426 | **4.48** | 639 |
| Tuesday | 474 | 3.12 | 220 |
| Wednesday | 176 | 2.92 | 94 |
| Thursday | 309 | 2.76 | 185 |
| Monday | 538 | 2.63 | 104 |
| Friday | 400 | 2.53 | 62 |
| Saturday | 409 | 2.19 | 45 |

Sunday is nearly 2x Saturday. Wednesday has lowest volume (176) but decent performance: untapped opportunity.

### Time of Day (UTC)

Best: 8 UTC (7.19 avg, midnight PST), 0 UTC (4.95 avg, 4pm PST)
Worst: 6 UTC (1.35 avg, 10pm PST)

---

## Performance by Project

| Project | Count | Avg Upvotes | Max |
|---------|-------|-------------|-----|
| **Vipassana** | 81 | **11.48** | 639 |
| AI Browser Profile | 33 | 4.70 | 92 |
| PieLine | 187 | 3.80 | 220 |
| (no project) | 953 | 3.15 | 167 |
| Assrt | 430 | 2.38 | 185 |
| Cyrano | 250 | 2.26 | 57 |
| Fazm | 488 | 2.20 | 94 |
| WhatsApp MCP | 50 | 2.18 | 34 |
| S4L | 43 | 2.07 | 22 |
| macOS MCP | 43 | 2.02 | 10 |
| Clone | 48 | 2.00 | 30 |
| macOS Session Replay | 41 | 1.63 | 6 |
| Terminator | 45 | 1.60 | 6 |
| mk0r | 32 | 1.56 | 4 |

## Performance by Subreddit

### Best (8+ avg upvotes, 3+ comments)

| Subreddit | Count | Avg Upvotes | Max |
|-----------|-------|-------------|-----|
| r/selfimprovement | 5 | 130.20 | 639 |
| r/doordash | 3 | 74.67 | 220 |
| r/cscareerquestions | 5 | 23.20 | 62 |
| r/privacy | 3 | 22.00 | 57 |
| r/KitchenConfidential | 5 | 12.60 | 59 |
| r/Mindfulness | 14 | 11.36 | 85 |
| r/Buddhism | 7 | 10.29 | 28 |
| r/ExperiencedDevs | 42 | 8.76 | 185 |
| r/vipassana | 14 | 8.36 | 35 |
| r/OpenAI | 9 | 8.00 | 31 |

### Worst (negative or near-zero avg, 3+ comments)

| Subreddit | Count | Avg Upvotes | Max |
|-----------|-------|-------------|-----|
| r/node | 3 | -4.00 | 1 |
| r/smallbusiness | 13 | -0.15 | 1 |
| r/SoftwareEngineering | 3 | 0.00 | 2 |
| r/reactjs | 11 | 0.64 | 2 |
| r/socialmedia | 4 | 0.75 | 1 |

### Highest Volume (watch for mod attention)

| Subreddit | All-time | Last 7d | Avg Upvotes |
|-----------|----------|---------|-------------|
| r/ClaudeCode | 320 | 42 | 2.92 |
| r/AI_Agents | 298 | 28 | 1.77 |
| r/ClaudeAI | 214 | 16 | 3.30 |
| r/webdev | 90 | 35 | 3.79 |

## Engagement Style Performance

| Style | Count | Avg Upvotes | Max |
|-------|-------|-------------|-----|
| contrarian | 5 | **7.00** | 30 |
| (no style) | 2,660 | 3.00 | 639 |
| critic | 4 | 1.75 | 4 |
| pattern_recognizer | 27 | 1.67 | 12 |
| data_point_drop | 10 | 1.00 | 2 |
| storyteller | 7 | 0.57 | 2 |
| curious_probe | 19 | **-0.11** | 5 |

---

## CRITICAL: Spam Risk Assessment

### Current Risk Level: HIGH

**55% of posts (384 of 694 measured) were made less than 1 minute after the previous post.** No human does this. Reddit's anti-spam system specifically detects sub-minute intervals.

### Dangerous Patterns

| Signal | Current | Safe Threshold |
|--------|---------|---------------|
| Daily volume | ~137 posts/day | 30-50 max |
| Max burst (single hour) | 33 posts | 5-8 max |
| Sub-minute posting gaps | 55% of posts | 0% (min 3-5 min gap) |
| Same-sub in one hour | Up to 25 (r/webdev) | 1-2 max |
| Posts at exactly 1 upvote | 58.6% | Normal is 30-40% |

### Posting Gap vs Performance

| Gap Between Posts | Count | Avg Upvotes |
|-------------------|-------|-------------|
| Under 1 min | 384 | 2.6 |
| **1-5 min** | 175 | **7.0** |
| 5-15 min | 43 | 2.9 |
| 15-30 min | 42 | 4.2 |
| 30 min+ | 50 | 1.9 |

The 1-5 min gap gets **2.7x the upvotes** of sub-minute posting. Slowing down literally improves quality.

### Same-Sub Rapid-Fire Penalties

| Subreddit | Rapid Posts (same sub, <1hr) | Avg Upvotes (rapid) | Avg Upvotes (spaced) |
|-----------|------------------------------|---------------------|---------------------|
| r/AI_Agents | 15 | 1.2 | **2.5** |
| r/reactjs | 6 | **0.2** | 1.3 |
| r/restaurantowners | 4 | **-1.5** | -0.5 |
| r/selfhosted | 2 | **-1.5** | 0.5 |

Rapid posting in the same sub consistently gets worse upvotes.

### Subreddit Concentration Risk

| Subreddit | Posts (Last 7d) | Risk |
|-----------|----------------|------|
| r/ClaudeCode | 42 (6/day) | HIGH: mods will notice |
| r/webdev | 35 (5/day) | HIGH |
| r/SideProject | 29 (4/day) | MEDIUM |
| r/AI_Agents | 28 (4/day) | MEDIUM |

### Potential Shadowban Indicator

58.6% of posts at exactly 1 upvote is elevated. Healthy engaged accounts are typically 30-40%. This could mean: (a) comments go unnoticed in low-traffic threads, (b) some are silently removed, or (c) quality is too low for most to attract votes.

---

## Reddit vs Twitter: Key Differences

1. **No viral window**: Reddit posts stay visible 12-24h vs Twitter's 2-6h peak. Thread age matters less.
2. **Promotion tolerance is near zero**: Twitter semi-tolerates self-promotion. Reddit communities actively punish it.
3. **Volume tolerance is much lower**: Reddit flags 10-20 comments/day as suspicious. We're doing 137/day.
4. **Thread scoring signals differ**: Subreddit fit and active discussion matter more than engagement velocity.
5. **OP replies matter more**: On Reddit, replying to OP gets 4.2x the engagement of replying to commenters.

---

## Recommended Changes

### Priority 0: Spam Risk Mitigation (URGENT)
1. **Enforce minimum 3-5 minute gap between ALL posts.** 55% of posts at sub-minute intervals is the single biggest threat to account survival.
2. **Reduce daily volume to 30-50 posts max.** 137/day is 7-14x what Reddit considers normal.
3. **Cap per-hour rate to 5-8 posts maximum.** Never exceed 10 in any hour.
4. **Cap same-subreddit posting to 2 per day.** r/ClaudeCode at 6/day and r/webdev at 5/day is too conspicuous.
5. **Investigate potential shadowban.** Check if comments are actually visible in incognito.

### Priority 1: Content Quality
6. **Go bimodal on length.** Either 1 punchy sentence (<100 chars, 6.03 avg) or 4-5 sentences of real substance (2.88 avg). Kill the 2-3 sentence middle ground.
7. **Start with "I" or "my".** First-person openings get 4.18 avg (37% above baseline) but only used 2.8% of the time.
8. **Never mention product names.** Caps upside at 10 upvotes, average drops from 3.05 to 1.17.
9. **Never include URLs in comments.** Average drops from 3.02 to 1.38.
10. **Remove curious_probe from Reddit styles.** Only style with negative avg upvotes.

### Priority 2: Targeting
11. **Prioritize replying to OP** instead of commenters. 10.82 avg vs 2.59 (4.2x difference), currently only 4.4% of comments.
12. **One comment per thread, strictly.** Multi-comment threads average 1.0-1.5 upvotes.
13. **Blacklist underperforming subreddits**: r/node, r/smallbusiness, r/SoftwareEngineering, r/reactjs, r/socialmedia, r/cursor, r/EndTipping, r/restaurantowners, r/texas.
14. **Weight toward proven subreddits**: r/selfimprovement, r/ExperiencedDevs, r/KitchenConfidential, r/Mindfulness, r/cscareerquestions, r/privacy, r/Buddhism, r/vipassana, r/OpenAI.
15. **Match project to subreddit strictly**: PieLine only in food/restaurant subs. Cyrano only in security/privacy. No cross-contamination.

### Priority 3: Structural Changes
16. **Increase run frequency to every 10-15 min, reduce posts per run to 3-5.** More frequent, smaller batches.
17. **Weight project selection toward winners**: Vipassana (11.48 avg), AI Browser Profile (4.70), PieLine in food subs (3.80).
18. **Post more on Sundays** (4.48 avg, nearly 2x Saturday). Tuesday is the best weekday (3.12).
19. **Post during 0-8 UTC window** (4.95-7.19 avg upvotes).
20. **Favor statements over questions.** Be authoritative, not inquisitive.

### Priority 4: Pipeline Improvements
21. **Fix human escalation bottleneck.** 14 DM conversations flagged for human takeover, only 6 replies sent. High-value leads going cold.
22. **Re-evaluate feedback reports.** Posts using feedback reports average 2.63 vs 3.19 without. Not clearly helping.
23. **Explore Wednesday as untapped day.** Lowest volume (176 posts) but decent performance (2.92 avg).
24. **Track and prioritize thread authors.** Deep_Ad1959's own threads yield 11.77 avg across 109 replies.
