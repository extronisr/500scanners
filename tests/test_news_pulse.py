"""news_pulse title dedupe: same story across outlets must collapse;
different stories that share generic words must survive."""
import news_pulse as np


def toks(title):
    return np.norm_title(title)


def test_cross_outlet_rewrite_same_story_is_dup():
    kept = [toks("Russia, Ukraine air raid alert apps hit by cyberattacks after truce ends")]
    assert np.is_dup(toks("Ukraine war briefing: Kyiv says air raid warning app likely hacked after users receive false alerts"), kept)


def test_exact_reword_jaccard_still_dup():
    kept = [toks("Global AI sell-off eases after Nasdaq's worst month in over a year")]
    assert np.is_dup(toks("Global AI sell-off eases after Nasdaq worst month in a year - CNBC"), kept)


def test_different_companies_earnings_not_dup():
    kept = [toks("Leidos (LDOS) Q3 earnings: what to expect")]
    assert not np.is_dup(toks("Fiserv (FISV) Q3 earnings: what to expect"), kept)


def test_same_event_different_angle_shares_too_little():
    kept = [toks("Fed officials signal patience on rate cuts")]
    assert not np.is_dup(toks("Fed's Powell says inflation path uncertain"), kept)


def test_hebrew_titles_not_dup_on_two_shared_words():
    kept = [toks("עליות באסיה ובחוזים בוול סטריט; מחיר הנפט יורד ב-3%")]
    assert not np.is_dup(toks("וול סטריט נערכת לשבוע נפיץ: כך המשקיעים מנסים להגן על עצמם"), kept)
