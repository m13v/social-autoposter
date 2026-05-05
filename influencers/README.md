# Influencer Outreach Pipeline

Subfolder for the freelancer / contractor posting pipeline. We hire a contractor, give them an email + phone we own, they create a fresh IG / TikTok account from their own device + IP, hand it over to our Meta Business portfolio, and then continue posting our content from it as a scoped Member.

## Operating model: contractor creates the account on their device, we hold business-level ownership

Password sharing violates Meta and TikTok ToS and gets accounts shadowbanned or nuked. Single-device multi-account creation gets the whole farm linked and mass-banned the moment one trips. So:

- Each account is created and operated from the contractor's own device + IP. We never spin them up from our MacBook.
- We provide a unique email and a unique phone number per account, and we forward SMS codes when IG / TikTok ask for them.
- After signup, the contractor immediately converts the account to Business. Then we add it to our Meta Business portfolio.
- We grant the contractor scoped Member access (Create content + Publish only). Day-to-day they never use the IG password again; they post via Business Suite signed in as themselves.

If they go rogue or we end the contract, we click Remove in People and they instantly lose access. They never had the password, so they can't lock us out.

## Per-account setup flow

1. **We provide:**
   - Unique email (Google Workspace alias on a domain we own, or a fresh Gmail)
   - Unique phone for SMS verification (real prepaid SIM is best; Google Voice is mid; Twilio / VoIP often gets blocked outright by IG)
   - Handle + brand brief

2. **Contractor creates the account on their own device + IP:**
   - Fresh IG install (or clean profile)
   - Sign up with the email we sent
   - Verify the phone: we receive the SMS code, forward it to them within 60 seconds
   - Set up profile per the brief

3. **Immediately convert to Business** (Settings, Account type and tools, Switch to professional account, Business)

4. **We add the account to our Meta Business portfolio:**
   - Business Suite, Settings, Instagram accounts, Add, sign in once with the IG creds we hold
   - Account is now an asset we own at the business layer

5. **We invite the contractor as a Member:**
   - Business Suite, Settings, People, Invite
   - Their personal email (NOT the email we created for the IG account)
   - Partial access: Create content + Publish content + Insights
   - OFF: Manage settings, Manage permissions, Manage messages (unless we explicitly want them on DMs)

6. **Contractor posts via Business Suite from their device, signed in as themselves.** The IG email + password becomes a root key kept in 1Password; we do not use it day-to-day.

## The phone number bottleneck

Each account needs a unique SMS-receivable number, and IG re-verifies on suspicious login / device change / password reset, so SMS forwarding is recurring, not one-time.

| Option | Cost | Survival rate | Notes |
|--------|------|---------------|-------|
| Real prepaid SIM (Mint, US Mobile, eSIM) | $5 to $15/mo | High | Best survival; real cellular network |
| Google Voice | Free | Medium | US only, 1 per Google account, sometimes flagged as VoIP |
| Twilio | Cheap at scale | Low | IG often blocks VoIP signups outright |
| SMS-Activate type services | Pennies | Very low | Burns hot, not recommended for production accounts |

Default to real prepaid eSIMs for any account we expect to keep alive past 30 days.

## Scaling

Start with 3 to 5 accounts, run them clean for 30 days, then scale. Going 0 to 30 in a week is a mass-ban setup regardless of how clean each individual signup is, because Meta's behavioral linking catches content + timing patterns even when device + IP look unique.

Staggering posting times across accounts and rotating caption templates is required, not optional.

## Files in this folder

- `README.md` (this file)
- `upwork-job-post.md`, JD draft for the Upwork listing
- `candidate-qualification.md`, triage checklist for incoming Upwork proposals
- `contractor-onboarding.md`, what to send the contractor on day 1

## Cost estimate (rough)

- Upwork client account: free
- Connects to post one job: about 6 (~$0.90)
- Phone numbers: $5 to $15/mo per account (real SIM lane)
- Email: free (Google Workspace alias)
- Contractor rate per published post, US-based: $5 to $15
- Contractor rate offshore (Philippines / LatAm): $2 to $5
- Estimated monthly burn at 5 accounts, daily posting, $5/post + $10/mo phone × 5: ~$800
