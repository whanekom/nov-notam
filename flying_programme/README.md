# Flying programme → WhatsApp

Every evening this checks a Gmail inbox for the NHV Ops "Flying Programme" email, finds the lines for one crew code (WIHAN) and sends them to your phone (Pushover notification or WhatsApp).

It handles the different ways Ops send the programme:

- **Table typed into the email** – read directly as text. Exact. Works whether crew and report time are separate columns or share one ("CREW AND CREW REPORT").
- **Picture of the spreadsheet** – read with OCR. Every crew cell is read twice; anything that doesn't agree, or a code one letter away from yours, is flagged in the message instead of being guessed.

The Ops email stays the authority. This is a convenience copy.

## What you get

The message says whether you are on the programme and gives only your own lines, including the code of whoever you are crewed with. Nobody else on the programme is listed.

On the programme (an invented example):

```
*Flying programme Sat 26 Sep*

WIHAN is on the programme (1 line):

*STD 08:00 PLATFORM A*
Report: 07:00
Aircraft: G-ABCD
Callsign: GOLD123
Crewmate: JOBLO

Details subject to change - the Ops email is the authority.
```

Not on it:

```
*Flying programme Sat 10 Oct*

WIHAN is not on the programme.
```

When a line gives one report time per pilot (`10:00/06:00`), yours is picked out by your position in the crew and the full cell is shown beside it.

You also get a warning if the email arrives but can't be read, if a code very close to yours appears, if a second programme arrives for the same date, or if no programme email has arrived by the 20:00 check.

A code close to yours that is simply another pilot's (ALERI beside ANVRI, say) is not flagged when it is written correctly. The list is the repository *variable* `OTHER_CODES`, codes separated by spaces or commas; it starts as `ALERI`.

The crewmate is shown by crew code only; no names are used anywhere.

To leave the crewmate out, add a repository *variable* `SHOW_CREW` set to `false`.

## Setup

The Gmail login is already in this repository: the job reuses the `SMTP_USER` / `SMTP_PASS` secrets the NOTAM job sends mail with. The programme email therefore has to arrive in **that** Gmail inbox.

### 1. Get the programme into Gmail

Either of these:

- **Ask Ops to add the Gmail address to the distribution list.** Several people already get it on personal addresses, so this is the simplest route.
- **Outlook rule.** Outlook on the web → Settings → Mail → Rules → Add new rule. Condition: *Subject includes* `flying programme`. Action: *Forward to* the Gmail address. Some companies block automatic forwarding outside the organisation; if the rule saves but nothing arrives, that is why.

### 2. Choose how the alert reaches the phone

The job sends by **Pushover** when its two secrets exist, and otherwise by **WhatsApp through CallMeBot**.

**Pushover** (official interface; app for iPhone and Android, one-off purchase after a 30-day trial):

1. Install the Pushover app and sign in. Your *user key* is on the pushover.net home page.
2. On pushover.net choose *Create an Application/API Token*, name it (for example "Flying programme") and copy the *API token* it gives you.

**WhatsApp through CallMeBot** (free, unofficial third-party service):

1. Save **+34 644 20 47 56** as a contact on your phone.
2. Send it this WhatsApp message: `I allow callmebot to send me messages`
3. It replies with your API key.

Either way the message text passes through that service.

### 3. Add the secrets

Repository → Settings → Secrets and variables → Actions → *New repository secret*:

| Secret | Value |
| --- | --- |
| `PUSHOVER_USER_KEY` | your Pushover user key |
| `PUSHOVER_APP_TOKEN` | the API token of the application you created |

or, for WhatsApp:

| Secret | Value |
| --- | --- |
| `WHATSAPP_PHONE` | your mobile with country code, digits only |
| `CALLMEBOT_APIKEY` | the key CallMeBot sent you |

Until one pair exists the job skips itself with a warning instead of failing. The crew code defaults to WIHAN; to change it, add a repository *variable* called `CREW_CODE`.

### 4. Test it

Actions tab → *Flying programme alert* → *Run workflow*, tick *Also send a short test message*, and run. A test message arrives within a minute, and any programme email in Gmail for today or later that hasn't been sent yet is processed as well.

## More than one pilot

Other pilots can get their own alert from the same nightly email. Each gets only their own lines, exactly as above, on their own phone.

1. The other pilot installs the Pushover app, signs in with their own account, and sends you their *user key* (on the pushover.net home page, 30 letters and digits starting with `u`).
2. Add one more repository secret called `EXTRA_PILOTS`. Its value is their crew code, an equals sign, and their user key, with one pilot per line (the codes and keys here are invented):

   ```
   JOBLO=uXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
   MASMI=uYYYYYYYYYYYYYYYYYYYYYYYYYYYYY
   ```

3. Run the test from step 4 above. Everyone gets a test message naming the crew code it is set up for.

It is a secret, rather than a setting in a file, so that other pilots' codes and keys never appear in this repository or its run logs; the logs call them "extra pilot 1", "extra pilot 2".

Extra pilots also get the warnings: when the email can't be read, and when no programme has arrived by the 20:00 check. If an extra pilot's key is rejected, the main pilot gets a message saying so.

## How it runs

- Checks between 18:00 and 20:00 UK time. A Claude scheduled task starts the checks at 18:02, 19:02 and 20:02 (once an hour is the most it allows). GitHub's own timer covers the half-hour checks (18:32, 19:32) and backs up the others, but it can run late or skip a slot. The "no programme email today" warning comes from the 20:02 check started by the scheduled task.
- Each email is handled once: it gets a `programme-alert-sent` label and is then moved to Gmail's Trash, which Gmail empties after 30 days. An email that could not be read is left in place. To keep handled emails, add a repository *variable* `KEEP_EMAILS` set to `true`.
- If sending fails, the run fails (GitHub emails you) and the next run tries again. With more than one pilot, anyone who already had the alert is noted on the email (a `programme-alert-part-…` label) so the retry only sends what is missing.
- GitHub pauses scheduled jobs in a repository with no activity for 60 days and emails you first; re-enable from the Actions tab.

## Run it by hand

```
python alert.py --eml saved-email.eml     # print the message for a saved email
python alert.py --dry-run                 # check Gmail, print instead of sending
```

Picture days need Tesseract (`sudo apt-get install tesseract-ocr`) and `pip install -r requirements.txt`. Table days need nothing beyond Python 3.10+.
