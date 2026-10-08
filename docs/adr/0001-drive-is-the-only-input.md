# Drive is the only input; the site's library is derived from it

Songs reach the site only by being uploaded to a source folder in Google Drive (YFM, ASE, MM). The site reads those folders with a read-only API key and works out its whole library from them: songs, arrangements, charts, IDs, merges. There is no editing on the site; anything it gets wrong is fixed by a correction in Drive (one corrections Sheet, plus hand-fixed `.cho` files), which the site reads like any other input.

We chose this over a site-owned catalogue that people edit because nobody on the teams will maintain one, and uploads to Drive are what they already do. It keeps the site rebuildable from Drive at any time, so the library needs no backup and no write access to anyone's Drive.

## Consequences

- The only state that can't be rebuilt is `sets.db`: sets, capos, share links, and the table that ties each song ID to its charts' Drive file IDs. It is deliberately not backed up.
- Existing songs keep today's slug as their ID; IDs are remembered against Drive file IDs so renames and title fixes don't break saved sets.
- The site checks the sources hourly (listing ~450 folders with an API key is slow, and it can't ask Drive what changed); **Sync now** covers "I just uploaded it".
- A fix takes a Drive edit by the maintainer, not a click on the site. That's accepted: problems are reported to the maintainer anyway.
