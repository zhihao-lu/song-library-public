"""Within a source, charts with the same music are one arrangement; different music is v2."""

from conftest import results, tagged
from make_charts import OTHER_CHORDS, cho, long_pdf, pdf, picture


def sync(client):
    return client.post("/api/library/sync").json


def folder(drive, song="Holy Ground"):
    return drive.path("YFM resources", "3. English Songs", song)


def shown(client, q):
    return [(r["tag"], r["source_file"]) for r in results(client, q)]


def test_the_same_chart_in_two_keys_is_one_arrangement(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground - G.pdf", pdf(cho("Holy Ground", key="G")))
    drive.upload(folder(drive), "Holy Ground - A.pdf", pdf(cho("Holy Ground", key="A")))
    sync(sources_client)
    (song,) = results(sources_client, "holy ground")
    assert song["tag"] == "YFM" and sorted(song["chart_keys"]) == ["A", "G"]


def test_a_one_page_and_a_two_page_version_are_one_arrangement_showing_the_one_page(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground 2pg.pdf", long_pdf(cho("Holy Ground")))
    drive.upload(folder(drive), "Holy Ground 1pg.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    assert shown(sources_client, "holy ground") == [("YFM", "Holy Ground 1pg.pdf")]


def test_a_readable_chart_beats_an_unreadable_one_in_the_same_key(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground scan.pdf", picture(pdf(cho("Holy Ground"))))
    drive.upload(folder(drive), "Holy Ground typed.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    assert shown(sources_client, "holy ground") == [("YFM", "Holy Ground typed.pdf")]


def test_an_unreadable_chart_of_a_known_song_is_another_key_of_its_arrangement(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground - G.pdf", pdf(cho("Holy Ground")))
    drive.upload(folder(drive), "Holy Ground - C.pdf", picture(pdf(cho("Holy Ground", key="C"))))
    sync(sources_client)
    (song,) = results(sources_client, "holy ground")
    assert song["source_file"] == "Holy Ground - G.pdf" and sorted(song["chart_keys"]) == ["C", "G"]


def test_a_pdf_beats_a_word_file_of_the_same_chart(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground (word).docx", pdf(cho("Holy Ground")))
    drive.upload(folder(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    assert shown(sources_client, "holy ground") == [("YFM", "Holy Ground.pdf")]


def test_otherwise_the_newest_upload_is_shown(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground old.pdf", pdf(cho("Holy Ground")))
    drive.upload(folder(drive), "Holy Ground new.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    assert shown(sources_client, "holy ground") == [("YFM", "Holy Ground new.pdf")]


def test_a_differently_arranged_chart_is_the_sources_v2(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    drive.upload(folder(drive), "Holy Ground (acoustic).pdf", pdf(cho("Holy Ground", **OTHER_CHORDS)))
    sync(sources_client)
    assert tagged(sources_client, "holy ground") == ["Holy Ground · YFM", "Holy Ground · v2 · YFM"]


def test_a_traditional_and_a_simplified_title_are_one_song(sources_client, drive):
    drive.upload(folder(drive, "YHWYDPW"), "永恆唯一的盼望.pdf", pdf(cho("永恆唯一的盼望")))
    drive.upload(folder(drive, "YHWYDPW"), "永恒唯一的盼望 (G).pdf", pdf(cho("永恒唯一的盼望", key="A")))
    sync(sources_client)
    (song,) = results(sources_client, "永恒唯一")
    assert sorted(song["chart_keys"]) == ["A", "G"]


def test_deleting_the_shown_chart_lets_the_next_one_show_and_numbers_stay_put(sources_client, drive):
    first = drive.upload(folder(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    drive.upload(folder(drive), "Holy Ground (acoustic).pdf", pdf(cho("Holy Ground", **OTHER_CHORDS)))
    drive.upload(folder(drive), "Holy Ground (acoustic, long).pdf", long_pdf(cho("Holy Ground", **OTHER_CHORDS)))
    sync(sources_client)
    shorter = drive.items[[i for i, f in drive.items.items() if f["name"] == "Holy Ground (acoustic).pdf"][0]]
    drive.delete(shorter["id"])
    drive.delete(first)
    sync(sources_client)
    assert shown(sources_client, "holy ground") == [("v2 · YFM", "Holy Ground (acoustic, long).pdf")]



def test_chords_over_other_words_are_still_the_same_music():
    """A Word file and the PDF made from it often put a chord over the next word along: the real
    sources' PDF/Word pairs scored 0.4-0.7 when chords were compared word by word."""
    from charts import music_fingerprint, same_music
    shifted = cho("Holy Ground", verses=[
        "Holy [G]is the Lord [C]our [D]God",
        "Every [Em]morning new [C]his [D]mercy",
        "Light [G]upon the hills [C]of [D]gold",
        "Hear [Em]the people sing [C]his [D]glory",
    ], chorus=["Halle[C]lu[G]jah, halle[D]lu[Em]jah", "Sing [C]it out [G]and sing [D]a[G]gain"])
    original = music_fingerprint(cho("Holy Ground"))
    assert same_music(original, music_fingerprint(shifted))
    assert not same_music(original, music_fingerprint(cho("Holy Ground", **OTHER_CHORDS)))


def test_a_title_with_an_artist_or_chords_after_it_is_the_same_song(sources_client, drive):
    drive.upload(folder(drive, "Gratitude"), "Gratitude (G).pdf", pdf(cho("Gratitude")))
    drive.upload(drive.path("MM ministry", "02 English Song Scores"), "Gratitude.pdf",
                 pdf(cho("Gratitude - Brandon Lake", **OTHER_CHORDS)))
    drive.upload(drive.path("MM ministry", "02 English Song Scores"), "Joy.pdf", pdf(cho("Gratitude Chords"), size=14))
    sync(sources_client)
    assert len({r["song_id"] for r in results(sources_client, "gratitude")}) == 1


def test_a_chart_titled_by_its_files_code_takes_its_song_folders_name(sources_client, drive):
    drive.upload(drive.path("ASE", "For All You've Done"), "FORALLY1 in G.pdf", pdf(cho("Forally1")))
    sync(sources_client)
    assert [r["title"] for r in results(sources_client, "for all")] == ["For All You've Done"]


def hillsong(drive, song):
    return drive.path("YFM resources", "7. Hillsong Digital Music Book", song)


def test_the_hillsong_music_book_never_adds_another_arrangement_of_a_known_song(sources_client, drive):
    drive.upload(folder(drive, "Alive"), "Alive (G).pdf", pdf(cho("Alive")))
    drive.upload(hillsong(drive, "Alive"), "Alive_LCC.pdf", pdf(cho("Alive", **OTHER_CHORDS)))
    sync(sources_client)
    assert shown(sources_client, "alive") == [("YFM", "Alive (G).pdf")]


def test_the_hillsong_music_book_gives_a_song_nobody_else_has_one_arrangement(sources_client, drive):
    drive.upload(hillsong(drive, "Embers"), "Embers_LCC.pdf", pdf(cho("Embers")))
    drive.upload(hillsong(drive, "Embers"), "Embers lead.pdf", pdf(cho("Embers", **OTHER_CHORDS)))
    sync(sources_client)
    assert [r["tag"] for r in results(sources_client, "embers")] == ["YFM"]


def test_a_hand_typed_chart_and_a_detailed_export_of_it_are_the_same_music():
    """As The Deer: the team's chart says G - D - Em; SongSelect's says G - D/F# - D - Em, C/D for D."""
    from charts import music_fingerprint, same_music
    plain = cho("As The Deer", verses=[
        "[G]As the [D]deer panteth [Em]for the [G]water",
        "So my [C]soul longs [D]after [G]You",
        "[G]You a[D]lone are my [Em]heart's de[G]sire",
        "And I [C]long to [D]worship [G]You",
    ])
    detailed = cho("As The Deer", verses=[
        "[G]As the [D/F#]deer [D]panteth [Em]for the [G/D]water",
        "So my [C]soul longeth [D7sus]af[D7]ter [G2sus]Thee [G]",
        "You a[D/F#]lone [D]are my [Em]heart's de[G/D]sire",
        "And I [C]long to [D]wor[C/D]ship [G]Thee [G/F#]",
    ])
    assert same_music(music_fingerprint(plain), music_fingerprint(detailed))
    assert not same_music(music_fingerprint(plain), music_fingerprint(cho("As The Deer", **OTHER_CHORDS)))


def test_chords_after_a_title_and_underscores_are_tidied_away(sources_client, drive):
    drive.upload(drive.path("MM ministry", "02 English Song Scores"), "Fall.pdf", pdf(cho("Fall Chords")))
    drive.upload(drive.path("MM ministry", "02 English Song Scores"), "Forever.pdf", pdf(cho("Forever_Bethel")))
    sync(sources_client)
    assert [r["title"] for q in ("fall", "forever") for r in results(sources_client, q)] == ["Fall", "Forever Bethel"]


TRADITIONAL = ["[G]讚美我的[C]主 祂的[D]愛永遠長存", "[Em]我們齊聲[C]歌頌 榮耀[D]歸給祢",
               "[G]聖靈充[C]滿這裡 [D]萬民都來敬拜", "[Em]從今直到[C]永遠 [D]讚美不斷"]
TRADITIONAL_CHORUS = ["[C]哈利路[G]亞 [D]讚美[Em]祢", "[C]祢是我的[G]王 [D]永遠的[G]主"]


def simplified(lines):
    from pdf2chordpro import to_simplified
    return [to_simplified(line) for line in lines]


def test_a_simplified_chart_is_shown_over_a_traditional_one_even_a_longer_one(sources_client, drive):
    drive.upload(folder(drive, "ZMWDZ"), "讚美我的主.pdf", pdf(cho("讚美我的主", verses=TRADITIONAL, chorus=TRADITIONAL_CHORUS)))
    drive.upload(folder(drive, "ZMWDZ"), "赞美我的主 (2 pages).pdf",
                 long_pdf(cho("赞美我的主", verses=simplified(TRADITIONAL), chorus=simplified(TRADITIONAL_CHORUS))))
    sync(sources_client)
    assert shown(sources_client, "赞美我的主") == [("YFM", "赞美我的主 (2 pages).pdf")]


# ---------------------------------------------------------------- one title, different songs

OTHER_SONG = {  # other words and other chords: another song that happens to share the title
    "verses": [
        "[Am]Rivers run to the [F]sea at [C]night [G]",
        "[Am]Lanterns burn on the [F]quay so [C]bright [E]",
        "[F]Sailors sing of the [G]tide and [Em]moon [Am]",
        "[Dm]Children dream they'll be [G]home by [C]noon [E7]",
    ],
    "chorus": ["[F]Carry me [G]over, [Em]carry me [Am]home", "[Dm]Over the [G]water [C]where I be[C/E]long"],
}
THIRD_CHORDS = {  # the first song's words again, on a third progression
    "verses": [
        "[D]Holy is the [A]Lord our [Bm]God",
        "[G]Every morning [D]new his [A]mercy",
        "[D]Light upon the [A]hills of [Bm]gold",
        "[G]Hear the people [D]sing his [A]glory",
    ],
    "chorus": ["[Bm]Hallelu[A]jah, [G]hallelu[D]jah", "[Em]Sing it [A]out and [D]sing a[A]gain"],
}


def test_two_songs_that_share_a_title_are_two_songs(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    drive.upload(folder(drive, "Holy Ground (sea)"), "Holy Ground.pdf", pdf(cho("Holy Ground", **OTHER_SONG)))
    sync(sources_client)
    found = results(sources_client, "holy ground")
    assert len(found) == 2 and len({s["song_id"] for s in found}) == 2
    assert [s["tag"] for s in found] == ["YFM", "YFM"]  # each its own song's first, not one's "v2"


def test_one_song_arranged_differently_is_still_one_song(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    drive.upload(folder(drive), "Holy Ground (acoustic).pdf", pdf(cho("Holy Ground", **OTHER_CHORDS)))
    sync(sources_client)
    assert len({s["song_id"] for s in results(sources_client, "holy ground")}) == 1


def test_no_two_arrangements_in_one_source_are_both_v2(sources_client, drive):
    mm = drive.path("MM ministry", "02 English Song Scores")
    acoustic = pdf(cho("Holy Ground", **OTHER_CHORDS))
    drive.upload(folder(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)                                              # YFM's v1
    drive.upload(folder(drive), "Holy Ground (acoustic).pdf", acoustic)
    sync(sources_client)                                              # YFM's v2
    drive.upload(mm, "Holy Ground (acoustic).pdf", acoustic)          # MM's copy of YFM's v2
    drive.upload(mm, "Holy Ground (piano).pdf", pdf(cho("Holy Ground", **THIRD_CHORDS)))  # MM's own
    sync(sources_client)
    tags = sorted(s["tag"] for s in results(sources_client, "holy ground"))
    assert tags == ["YFM", "v2 · YFM · MM", "v3 · MM"]


def test_a_picture_moved_by_a_same_as_row_doesnt_join_the_two_songs_again(sources_client, drive):
    from test_corrections import corrections
    drive.upload(folder(drive, "Holy Ground (sea)"), "Holy Ground.pdf", pdf(cho("Holy Ground", **OTHER_SONG)))
    drive.upload(folder(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))  # newer: the best chart
    # A picture of the sea song, in a folder of its own: by itself it would join the first song.
    drive.upload(folder(drive, "Holy Ground (scan)"), "Holy Ground.pdf", picture(pdf(cho("Holy Ground", **OTHER_SONG))))
    sync(sources_client)
    sea = next(s for s in results(sources_client, "holy ground") if "(sea)" in s["folder"])
    corrections(drive, f"3. English Songs/Holy Ground (scan)/Holy Ground.pdf,,,,,{sea['slug']},,")
    sync(sources_client)
    found = results(sources_client, "holy ground")
    assert len({s["song_id"] for s in found}) == 2  # still two songs
