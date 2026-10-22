# Submitting an Eggy Cup

Played the cup? Your game log has every round of it. Sending it takes a minute and puts the cup on the rankings without anyone having to chase logs on Discord.

Page: https://aizpunr.github.io/EggyCup-Rankings/submit.html (also linked as "Submit a cup" on the rankings page).

## 1. Grab the log

The file is `C:\Program Files (x86)\Steam\steamapps\common\Zeepkist\BepInEx\LogOutput.log`.

Zeepkist overwrites it every time the game starts, so copy it **before** relaunching. If you left the cup early, send it anyway: when several people send the same cup, the most complete log wins.

## 2. Fill in the details

- **Cup number**: the one Wheelie announced. The page suggests the next one.
- **Cup date**: the Saturday in Spain, which is the Friday night of the cup in the US.
- **Map name**: optional.
- **Mapper**: if the mapper was in the lobby, pick them from the list so they do not count as a player. If not, type the name.
- **Also exclude**: only people who were in the lobby but did not race (testers, a host who only hosted, someone who left before round 1). Leave everyone else in.

## 3. Check the result

The page reads your log right in the browser and shows the winner, the podium and the full leaderboard. It then asks one question:

- A normal cup: "Does the result below match what happened?"
- Both finalists did not finish the final: who won? The house rule says 3rd place wins; or pick who won a replay of the final.
- The cup stopped with several players still in (usually a host disconnect): rank them by their last-round times?
- The log has more than one cup start (false start or redo): which one was the real cup?

If you are not sure, say so. The cup then goes to aizpun, who sorts it out by hand.

## 4. Send it

Pass the anti-bot check and press Submit. The status table on the page shows what happens next:

| Status | Meaning |
|---|---|
| received | Waiting for the automatic job (every 15 minutes from the end of the cup until Sunday midday, Spain time). |
| processed | Ratings computed. Waiting for aizpun to check and publish. |
| published | Live on the rankings. |
| review | aizpun will look at it by hand. |
| duplicate | That cup was already processed, or this exact file was already sent. |
| superseded | Someone sent a more complete log for the same cup. |
| failed | The pipeline stopped. The note says why; ping aizpun. |

## Privacy

The full log is stored privately and never published. Only the cup tracker lines (round leaderboards and eliminations) go into the public repository. No IP address is stored.
