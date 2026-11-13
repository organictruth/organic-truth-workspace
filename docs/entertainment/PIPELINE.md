# Organic Truth Entertainment — music-to-video pipeline

Organic Truth Entertainment releases the music. Organic Truth Vibration Studio makes the video. This is the workspace record of that pipeline.

Matthew set this order on Nov 13, 2026. It is the order to follow. A later step starts when the handoff from the previous step is done.

The store fleet and channel map live in [FLEET.md](../../FLEET.md) and [CHANNELS.md](../../CHANNELS.md). The store is a different business.

## Standing rules

- Organic Truth Entertainment is separate from the Organic Truth store. Never mix money, accounts, paperwork, or public materials, and no store domain on Entertainment materials.
- No watermark stripping and no hiding AI origin.
- No invented lyrics.
- Only the final master gets copyrighted. Drafts, roughs, stems, and pre-masters are never registered.

Titles, writers, the claimant, credits, and splits come from Matthew. Bots do not invent them.

## Order

| Step | Who | Receives | Hands off |
| --- | --- | --- | --- |
| 1a | [The Beat](./bots/the-beat.md) | Suno tracks | Finished track and stems, to The Master |
| 1b | [The Master](./bots/the-master.md) | Finished track and stems from The Beat | Final locked master, to The Seal |
| 2 | [The Seal](./bots/the-seal.md) | Final locked master from The Master | Registration log, to Matthew |
| 3 | Matthew, on DistroKid | Final locked master and artwork | The DistroKid upload |
| 4 | Matthew, at BMI | Title, writers, and splits | Writer registration, then the song registration |
| 5 | [The Sync](./bots/the-sync.md), [The Frame](./bots/the-frame.md), [The Reel](./bots/the-reel.md) | Finished master, after step 4 | Written video, to The Cut and Framecut |
| 6 | [The Cut](./bots/the-cut.md) and [Framecut](./bots/framecut.md) | Written video from The Reel | Edited video, to Matthew |
| 7 | [PressSEO](./bots/pressseo.md) and [Amppost](./bots/amppost.md) | Approved video and story from Matthew | Published Entertainment long-form and social/channel posts |

DistroKid and BMI are services Matthew uses. They are not bots, and they have no page under `bots/`.

## 1. The Beat, then The Master

### The Beat

[The Beat](./bots/the-beat.md) finishes the track and breaks Suno tracks into stems.

The Beat receives the Suno tracks for the song. No earlier bot hands them off. The Beat hands the finished track and the stems to The Master only. Stems stop there. They do not go to The Seal, DistroKid, BMI, or Vibration Studio.

### The Master

[The Master](./bots/the-master.md) masters that finished track in Ableton. Ozone 12 is on the master bus only. True peak is at or below -1 dBTP. Delivery is 48 kHz/24-bit.

That delivery is the final locked master. The Master hands it to The Seal. The same file is the one Matthew later uploads and the one The Sync places in DaVinci Resolve/Fairlight. Pre-masters are not handed on.

## 2. The Seal

[The Seal](./bots/the-seal.md) is the copyright bot. She registers only the final locked master with the U.S. Copyright Office, through the Standard Application on [copyright.gov](https://www.copyright.gov/) (eCO).

The quoted fee is $65. She confirms the current fee on copyright.gov before paying.

When the same claimant owns both the composition and the sound recording, and both are in that master, one filing on Form SR covers both. The combined-filing conditions and the Author Created entries are on [her page](./bots/the-seal.md). She files only after Matthew has identified the human-authored contribution the claim covers. She logs every registration with the title, writer(s), claimant, date, service request number, confirmation or registration number, and status.

She completes the eCO submission before Matthew uploads that master to DistroKid: the fee is paid, the final locked master is uploaded as the deposit, and the service request number is logged. When the Office later issues a confirmation or registration number, she adds it to the same log entry and updates the status.

Drafts, roughs, stems, and pre-masters are never registered. AI-generated material is disclosed and excluded as the Copyright Office requires.

## 3. DistroKid

Matthew uploads the final locked master and the artwork to DistroKid. He fills in the AI-disclosure and credit fields honestly. He can add the Beatport add-on for electronic tracks.

He receives the locked master after The Seal has completed the eCO submission, and he supplies the artwork. He hands off the completed DistroKid upload. The next step is BMI.

## 4. BMI

Matthew handles performance royalties at BMI. He completes writer registration first, then registers each song with its title, writers, and splits.

He receives those facts for the song that now has a DistroKid upload. He hands off the writer registration and the song registration. Vibration Studio starts after this.

## 5. The Sync, The Frame, and The Reel

This is Organic Truth Vibration Studio.

[The Sync](./bots/the-sync.md) receives the finished master from Matthew and places it into DaVinci Resolve/Fairlight. The Sync waits until the DistroKid upload and the BMI registration are done, then hands that session to The Frame and The Reel.

[The Frame](./bots/the-frame.md) directs. The Frame receives the session from The Sync and hands direction to The Reel.

[The Reel](./bots/the-reel.md) writes the video. The Reel receives The Frame's direction and the session The Sync prepared, and hands the written video to The Cut and Framecut.

## 6. The Cut and Framecut

[The Cut](./bots/the-cut.md) and [Framecut](./bots/framecut.md) edit the video. Both receive the written video from The Reel. They hand the edited video to Matthew.

Framecut's store visual-pack work is a different job. It stays in [FLEET.md](../../FLEET.md).

## 7. PressSEO and Amppost

Matthew approves the edited video and the story that goes with it. [PressSEO](./bots/pressseo.md) and [Amppost](./bots/amppost.md) push only that approved video and story.

PressSEO publishes it as Entertainment long-form. Amppost publishes it across Entertainment social and channel outlets. Both use Entertainment accounts. They do not use store accounts, and they do not put the store domain on the posts.

They are the end of this pipeline.
