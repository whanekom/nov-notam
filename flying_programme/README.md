# Flying programme → WhatsApp

Every evening this checks a Gmail inbox for the NHV Ops "Flying Programme" email, finds the lines for one crew code (WIHAN) and sends them to WhatsApp.

It handles both ways Ops send the programme:

- **Table typed into the email** – read directly as text. Exact.
- **Picture of the spreadsheet** – read with OCR. Every crew cell is read twice; anything that doesn't agree, or a code one letter away from yours, is flagged in the message instead of being guessed.

The Ops email stays the authority. This is a convenience copy.

## What you get

On the programme:

```
*Flying programme Sat 26 Sep*

WIHAN is on 1 line:

*STD 08:00 WENH*
Report: 07:00
Aircraft: G-NHVV
Callsign: GOLD651
Crew: WIHAN/IAGOU

Details subject to change - the Ops email is the authority.
```

Not on it (the crew list is there so a misread is obvious):

```
*Flying programme Sat 10 Oct*

WIHAN is not on the programme.

Crew read from the table: ZOMUN, CHGUE, DAALL, ALERI, MABRO
```

You also get a warning if the email arrives but can't be read, if a second programme arrives for the same date, or if no programme email has arrived by 20:30 UTC.

## Setup

The Gmail login is already in this repository: the job reuses the `SMTP_USER` / `SMTP_PASS` secrets the NOTAM job sends mail with. The programme email therefore has to arrive in **that** Gmail inbox.

### 1. Get the programme into Gmail

Either of these:

- **Ask Ops to add the Gmail address to the distribution list.** Several people already get it on personal addresses, so this is the simplest route.
- **Outlook rule.** Outlook on the web → Settings → Mail → Rules → Add new rule. Condition: *Subject includes* `flying programme`. Action: *Forward to* the Gmail address. Some companies block automatic forwarding outside the organisation; if the rule saves but nothing arrives, that is why.

### 2. Switch on WhatsApp messages (CallMeBot)

1. Save **+34 644 20 47 56** as a contact on your phone.
2. Send it this WhatsApp message: `I allow callmebot to send me messages`
3. It replies with your API key.

CallMeBot is a free third-party service for personal use; the message text passes through it.

### 3. Add two secrets

Repository → Settings → Secrets and variables → Actions → *New repository secret*:

| Secret | Value |
| --- | --- |
| `WHATSAPP_PHONE` | your mobile with country code, e.g. `+447...` |
| `CALLMEBOT_APIKEY` | the key from step 2 |

Until both exist the job skips itself with a warning instead of failing. The crew code defaults to WIHAN; to change it, add a repository *variable* called `CREW_CODE`.

### 4. Test it

Actions tab → *Flying programme alert* → *Run workflow*. Any programme email in Gmail for today or later that hasn't been sent yet gets processed, and the WhatsApp arrives within a minute.

## How it runs

- Checks every 15 minutes from 15:00 to 19:00 UTC, then hourly until 21:00 UTC. GitHub's timer can run 5–20 minutes late.
- Each email is handled once; Gmail gets a `programme-alert-sent` label on it.
- If WhatsApp sending fails, the run fails (GitHub emails you) and the next run tries again.
- GitHub pauses scheduled jobs in a repository with no activity for 60 days and emails you first; re-enable from the Actions tab.

## Run it by hand

```
python alert.py --eml saved-email.eml     # print the message for a saved email
python alert.py --dry-run                 # check Gmail, print instead of sending
```

Picture days need Tesseract (`sudo apt-get install tesseract-ocr`) and `pip install -r requirements.txt`. Table days need nothing beyond Python 3.10+.
