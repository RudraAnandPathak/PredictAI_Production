"""Modelled source material for the research-intelligence tests: a small Wikipedia, an arXiv listing, general web pages and a French Revolution PDF.

Everything here is a STAND-IN written for these tests in the documented response shapes of the MediaWiki Action API and the arXiv Atom API. It is NOT a recording of the live services, and the
article texts are short summaries written for the tests (not copies of Wikipedia). What it models on purpose: pages that share words with the question but are about something else (Louis XVIII,
a television series, a furniture style, a city, a physics paper written by an author named Louis) because those are the cases a word-overlap ranking gets wrong.

Facts used in the history texts are ordinary, well-established ones (dates of reigns, the Estates General of 1789, the Assembly of Notables of 1787, and so on)."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------------------------------------------------------------------------- Wikipedia
Section = Tuple[int, str, str, List[str]]          # (heading level, anchor id, heading, paragraphs)


def article_html(lead: List[str], sections: List[Section]) -> str:
    parts = ['<div class="mw-content-ltr mw-parser-output">']
    parts += [f"<p>{p}</p>" for p in lead]
    for level, anchor, heading, paras in sections:
        parts.append(f'<div class="mw-heading mw-heading{level}"><h{level} id="{anchor}">{heading}</h{level}></div>')
        parts += [f"<p>{p}</p>" for p in paras]
    parts.append("</div>")
    return "".join(parts)


def page(pid: int, title: str, shortdesc: str, categories: List[str], lead: List[str], sections: List[Section], *, wordcount: int = 4000, disambiguation: bool = False,
         revid: int = 0) -> Dict[str, Any]:
    return {"pageid": pid, "title": title, "shortdesc": shortdesc, "categories": categories, "lead": lead, "sections": sections, "wordcount": wordcount, "disambiguation": disambiguation,
            "revid": revid or 9000 + pid, "html": article_html(lead, sections), "extract": " ".join(lead)}


LOUIS_XVI = page(
    1001, "Louis XVI", "King of France from 1774 to 1792",
    ["Kings of France", "House of Bourbon", "French Revolution", "18th-century French monarchs", "Executed French people"],
    ["<b>Louis XVI</b> (23 August 1754 – 21 January 1793) was the last king of France before the fall of the monarchy during the French Revolution. He reigned from 10 May 1774 and was king "
     "of the French from 1791 until the monarchy was abolished on 21 September 1792; he was executed by guillotine on 21 January 1793.",
     "His reign was dominated by a financial crisis that grew out of the cost of earlier wars, by resistance of the privileged orders to tax reform, and by the political crisis that led to the "
     "meeting of the Estates General in 1789."],
    [(2, "Early_life", "Early life", ["Louis-Auguste became Dauphin in 1765 when his father died, and he married the Austrian archduchess Marie Antoinette on 16 May 1770. He became king at the age of nineteen "
                                      "when his grandfather Louis XV died in May 1774."]),
     (2, "Reign", "Reign", ["Louis XVI inherited a monarchy that was described as absolute but was constrained in practice by custom, by the regional courts and by the state's debts."]),
     (3, "Royal_authority", "Royal authority and government", [
         "As king, Louis XVI held formal authority over legislation, taxation, war and the appointment of ministers, which he exercised through a royal council and secretaries of state. In practice the "
         "Parlements, the high courts of justice, could refuse to register royal edicts and so delay reform, and they became a centre of opposition to the crown's tax proposals.",
         "Louis dismissed ministers who proposed reform when they met strong resistance at court, which gave his government a reputation for indecision. Contemporaries blamed him, the court and Queen "
         "Marie Antoinette for the failures of the crown's finances."]),
     (3, "Finances_and_taxation", "Finances and taxation", [
         "The crown's finances were strained by the cost of earlier wars and by French support for the American Revolutionary War. Successive ministers, among them Turgot, Necker, Calonne and Brienne, "
         "proposed reforms including a more equal land tax, but these met resistance from the privileged orders, so the debt continued to grow and interest payments absorbed a large share of royal revenue.",
         "In 1787 the Assembly of Notables refused to approve Calonne's proposals for a uniform land tax. The Parlement of Paris also refused, and in 1788 the king agreed to summon the Estates General "
         "for May 1789, the first meeting since 1614."]),
     (3, "Foreign_policy", "Foreign policy and the American war", [
         "France entered the American War of Independence in 1778, supplying loans, arms, an army and a navy to the colonists in order to weaken Britain. The war ended with the Treaty of Paris in 1783, "
         "but it had been paid for largely by borrowing, and it added greatly to the state's debt without restoring the crown's finances."]),
     (3, "Society_and_estates", "Society and the three estates", [
         "French society was divided into three estates: the clergy, the nobility and everyone else. The first two estates enjoyed exemptions from many direct taxes, while the Third Estate, which included "
         "peasants, urban workers and the bourgeoisie, carried most of the tax burden and had little political voice.",
         "Bad harvests in 1788 raised the price of bread and deepened hardship in the towns and the countryside just as the political crisis came to a head."]),
     (2, "Revolution", "Revolution and fall of the monarchy", [
         "The Estates General opened at Versailles on 5 May 1789. On 17 June the Third Estate declared itself the National Assembly and on 20 June its members swore the Tennis Court Oath. The storming of "
         "the Bastille on 14 July 1789 marked the start of open revolution in Paris.",
         "The royal family attempted to flee in June 1791 and was stopped at Varennes. After the Constitution of 1791 made him a constitutional king, Louis declared war on Austria in April 1792; the "
         "Tuileries Palace was stormed on 10 August 1792 and the monarchy was suspended."]),
     (2, "Execution", "Trial and execution", ["The National Convention tried Louis for treason and he was executed on 21 January 1793."]),
     (2, "See_also", "See also", ["Louis XVI style"]), (2, "References", "References", ["Smith, J. (2001). The Last Bourbon Kings. Press."])],
    wordcount=14000)

LOUIS_XVIII = page(
    1002, "Louis XVIII", "King of France from 1814 to 1824",
    ["Kings of France", "House of Bourbon", "Bourbon Restoration", "19th-century French monarchs"],
    ["<b>Louis XVIII</b> (17 November 1755 – 16 September 1824), known as \"the Desired\", was King of France from 1814 to 1824, except for a period in 1815 known as the Hundred Days. He was the younger "
     "brother of Louis XVI, who was executed in 1793, and spent the Revolutionary and Napoleonic years in exile.",
     "After Napoleon's first abdication he returned to France and ruled as a constitutional monarch under the Charter of 1814."],
    [(2, "Exile", "Exile", ["Louis Stanislas Xavier left France in 1791 and lived in several European courts. He proclaimed himself king after the death of his nephew Louis XVII in 1795."]),
     (2, "Restoration", "Restoration", ["The Allied powers and the Senate recalled the Bourbons in 1814. Louis XVIII entered Paris in May 1814 and granted the Charter of 1814, which created a parliament with two "
                                       "chambers and guaranteed religious toleration while keeping the king's right to propose laws."]),
     (2, "Hundred_Days", "Hundred Days", ["Napoleon returned from Elba in March 1815 and Louis fled to Ghent. After Waterloo he returned in July 1815."]),
     (2, "Domestic_policy", "Domestic policy", ["His government balanced ultra-royalists against moderates. The White Terror of 1815 targeted supporters of Napoleon, and the war indemnity imposed by the Allies burdened the treasury."]),
     (2, "Death", "Death", ["Louis XVIII died on 16 September 1824 and was succeeded by his brother Charles X."]), (2, "References", "References", ["Smith, J. (2005). The Restoration. Press."])],
    wordcount=9000)

LOUIS_XIV = page(
    1003, "Louis XIV", "King of France from 1643 to 1715", ["Kings of France", "House of Bourbon", "17th-century French monarchs"],
    ["<b>Louis XIV</b> (5 September 1638 – 1 September 1715), known as the Sun King, was King of France from 1643 until his death in 1715. His reign of 72 years is among the longest in European history."],
    [(2, "Absolute_monarchy", "Absolute monarchy", ["Louis XIV centralised royal power and built the Palace of Versailles. His minister Colbert reorganised the finances, but the wars of the later reign left heavy debts."]),
     (2, "References", "References", ["Jones, A. (1999). The Sun King. Press."])], wordcount=16000)

LOUIS_XVII = page(
    1004, "Louis XVII", "Titular King of France (1785–1795)", ["House of Bourbon", "French Revolution"],
    ["<b>Louis XVII</b> (27 March 1785 – 8 June 1795) was the son of Louis XVI and Marie Antoinette. He was the titular King of France from 1793 and died in the Temple prison in Paris."],
    [(2, "Imprisonment", "Imprisonment", ["He was held in the Temple prison with his family after the monarchy fell, and died of illness aged ten."]), (2, "References", "References", ["Brown, K. (2010). The Lost Dauphin. Press."])], wordcount=6000)

MARIE_ANTOINETTE = page(
    1005, "Marie Antoinette", "Last queen of France before the French Revolution", ["Queens consort of France", "House of Habsburg-Lorraine", "French Revolution"],
    ["<b>Marie Antoinette</b> (2 November 1755 – 16 October 1793) was the last queen of France before the French Revolution. She was the wife of Louis XVI and was executed in October 1793."],
    [(2, "Public_image", "Public image", ["She became unpopular because of her Austrian birth and her spending, and was mocked as \"Madame Déficit\" during the financial crisis."]), (2, "References", "References", ["Lee, C. (2003). The Queen. Press."])],
    wordcount=12000)

FRENCH_REVOLUTION = page(
    1006, "French Revolution", "Revolution in France from 1789 to 1799", ["French Revolution", "18th-century revolutions", "1789 in France"],
    ["The <b>French Revolution</b> was a period of political and societal change in France that began with the Estates General of 1789 and ended with the formation of the French Consulate in November 1799. "
     "Its causes lay in the financial crisis of the monarchy, the inequalities of the three estates and the spread of Enlightenment ideas."],
    [(2, "Causes", "Causes", ["The Revolution had political, economic and social causes. The state was deeply in debt after the Seven Years' War and the American War of Independence, and attempts to reform taxation "
                              "were blocked by the privileged orders and the Parlements. Poor harvests in 1788 raised bread prices."]),
     (2, "Estates_General", "Estates General and the National Assembly", ["The Estates General met in May 1789. The Third Estate declared itself the National Assembly in June and the Bastille was stormed on 14 July."]),
     (2, "Terror", "Reign of Terror", ["From 1793 the Committee of Public Safety, led by Robespierre, used mass executions against perceived enemies of the Republic."]),
     (2, "Legacy", "Legacy", ["The Revolution abolished feudal privileges and issued the Declaration of the Rights of Man and of the Citizen in August 1789."]), (2, "References", "References", ["Doyle, W. (1989). Oxford History. Press."])],
    wordcount=20000)

CAUSES = page(
    1007, "Causes of the French Revolution", "Factors that led to the French Revolution of 1789", ["French Revolution", "Causes of revolutions"],
    ["The <b>causes of the French Revolution</b> are generally grouped into political, social, economic and intellectual factors, together with the events of 1788 and 1789 that triggered it."],
    [(2, "Political", "Political causes", ["The monarchy's authority was challenged by the Parlements, and the king's ministers could not persuade the privileged orders to accept reform. The failure of the Assembly of Notables in 1787 led to the summoning of the Estates General."]),
     (2, "Economic", "Economic causes", [
         "The crown was in a deep fiscal crisis. France's participation in the Seven Years' War and the American Revolutionary War left it heavily indebted, and by the late 1780s interest on the debt consumed roughly half of royal expenditure.",
         "The tax system was uneven. The taille, the gabelle and the corvée fell mainly on peasants, while the clergy and nobility were largely exempt from direct taxes, so the crown could not raise enough revenue.",
         "A poor harvest in 1788, a hard winter and rising bread prices increased hunger and unrest in the towns before the Revolution began."]),
     (2, "Social", "Social causes", ["Society was divided into three estates. The bourgeoisie of the Third Estate grew in wealth and education but lacked political power and resented noble privilege."]),
     (2, "Intellectual", "Intellectual causes", ["Enlightenment writers such as Montesquieu, Voltaire and Rousseau criticised arbitrary power and privilege, and the American Revolution offered an example of a republic."]),
     (2, "Triggers", "Immediate triggers", ["The immediate events were the calling of the Estates General, the deadlock over voting by order, the Tennis Court Oath on 20 June 1789 and the fall of the Bastille on 14 July 1789."]),
     (2, "References", "References", ["Doyle, W. (2001). Origins. Press."])], wordcount=7000)

ANCIEN_REGIME = page(
    1008, "Ancien Régime", "Political and social system of the Kingdom of France before 1789", ["Ancien Régime", "Kingdom of France", "History of France by period"],
    ["The <b>Ancien Régime</b> was the political and social system of the Kingdom of France from the late Middle Ages until 1789. It was characterised by the divine-right monarchy and a society of three orders."],
    [(2, "Society", "Society", ["The population was divided into three estates: the clergy, the nobility and the Third Estate, which made up the great majority of the population."]),
     (2, "Taxation", "Taxation", ["Direct taxes such as the taille fell on commoners, while nobles and clergy had exemptions. The crown also relied on indirect taxes such as the gabelle on salt, which were collected by private tax farmers."]),
     (2, "Government", "Government", ["The king ruled through councils and intendants, and the Parlements registered royal edicts. The Estates General had not met since 1614."]),
     (2, "References", "References", ["Behrens, C. (1985). Society, Government and the Enlightenment. Press."])], wordcount=5000)

BOURBON_RESTORATION = page(
    1009, "Bourbon Restoration in France", "French regime from 1814 to 1830", ["Bourbon Restoration", "19th century in France"],
    ["The <b>Bourbon Restoration</b> was the period of French history from the fall of Napoleon in 1814 to the July Revolution of 1830, during which the House of Bourbon ruled under Louis XVIII and Charles X."],
    [(2, "Louis_XVIII", "Reign of Louis XVIII", ["Louis XVIII ruled under the Charter of 1814, a constitutional framework with two chambers."]), (2, "References", "References", ["Pilbeam, P. (1995). Restoration France. Press."])], wordcount=5000)

LOUIS_XVI_STYLE = page(
    1010, "Louis XVI style", "Neoclassical style of architecture, furniture and decoration", ["Neoclassical architecture", "French furniture", "Decorative arts"],
    ["<b>Louis XVI style</b> is a neoclassical style of architecture, furniture, decoration and the visual arts that developed in France during the reign of Louis XVI. It favoured straight lines, "
     "geometric forms and motifs taken from ancient Greece and Rome."],
    [(2, "Furniture", "Furniture", ["Furniture makers such as Jean-Henri Riesener produced commodes with marquetry, bronze mounts and fluted legs. Walnut and mahogany were widely used."]),
     (2, "References", "References", ["Watson, F. (1960). Louis XVI Furniture. Press."])], wordcount=3000)

DTF = page(
    1011, "DTF St. Louis", "American television limited series", ["American television limited series", "2026 American television series debuts", "Television shows set in St. Louis"],
    ["<i>DTF St. Louis</i> is an American television limited series set in the St. Louis area, starring a cast of well-known actors. It premiered in 2026 and follows a group of friends and a local investigation."],
    [(2, "Premise", "Premise", ["The series follows three adults in the suburbs of St. Louis, Missouri."]), (2, "Cast", "Cast", ["The principal cast includes several actors."]),
     (2, "References", "References", ["TV Guide. (2026). Review. Press."])], wordcount=2500)

ST_LOUIS = page(
    1012, "St. Louis", "City in and independent city in Missouri, United States", ["Cities in Missouri", "St. Louis"],
    ["<b>St. Louis</b> is the second-largest city in Missouri, United States. It is named after Louis IX of France, the patron saint of the city's namesake cathedral."],
    [(2, "History", "History", ["The city was founded in 1764 by French fur traders and was part of Spanish and French Louisiana before the Louisiana Purchase in 1803."]), (2, "References", "References", ["City records."])],
    wordcount=18000)

LOUIS_DISAMBIG = page(
    1013, "Louis", "Disambiguation page", ["Disambiguation pages"],
    ["<b>Louis</b> may refer to:"], [(2, "Kings", "Kings of France", ["Louis XIV", "Louis XVI", "Louis XVIII"])], wordcount=300, disambiguation=True)

ALL_PAGES: List[Dict[str, Any]] = [LOUIS_XVI, LOUIS_XVIII, LOUIS_XIV, LOUIS_XVII, MARIE_ANTOINETTE, FRENCH_REVOLUTION, CAUSES, ANCIEN_REGIME, BOURBON_RESTORATION, LOUIS_XVI_STYLE, DTF, ST_LOUIS,
                                   LOUIS_DISAMBIG]
REDIRECTS = {"louis xvi of france": "Louis XVI", "louis 16": "Louis XVI", "louis xviii of france": "Louis XVIII", "french revolution of 1789": "French Revolution", "ancien regime": "Ancien Régime"}

# A MediaWiki full-text search is ranked by a relevance model this file cannot reproduce. These are the orders a poor ranker could plausibly return, written down so that tests can prove the
# engine does not depend on the search engine putting the right page first.
ADVERSARIAL_ORDER: Dict[str, List[str]] = {
    "louis xvi": ["Louis XVIII", "Louis XIV", "DTF St. Louis", "Louis XVI style", "Louis XVI", "Louis XVII", "Marie Antoinette", "St. Louis"],
    "france": ["Bourbon Restoration in France", "Louis XVIII", "St. Louis", "DTF St. Louis", "French Revolution"],
    "louis xvi france": ["DTF St. Louis", "Louis XVIII", "Louis XVI style", "St. Louis", "Louis XVI"],
    "louis xviii": ["Louis XVI", "Louis XVIII", "Bourbon Restoration in France"],
    "french revolution": ["French Revolution", "Causes of the French Revolution", "Louis XVI", "Ancien Régime"],
}

# ---------------------------------------------------------------------------------------------------------------------------------------------- arXiv
# (id with version, title, abstract, authors, primary category, extra categories, affiliation)
ARXIV_ENTRIES: List[Dict[str, Any]] = [
    {"id": "2301.00001v1", "title": "Ordering kinetics and magnetic anisotropy in CoPt alloy thin films",
     "summary": "We study L1_0 ordering in equiatomic CoPt alloy thin films grown by sputtering. Magnetic anisotropy was measured at a synchrotron beamline in Grenoble, France, and compared with a "
                "model of chemical ordering. The measurements follow the framework introduced by Louis Néel for the pair-ordering anisotropy of alloys. Results were presented at the XVI International Conference on Magnetic Materials.", "authors": ["Louis Fabre", "Anna K."],
     "cat": "cond-mat.mtrl-sci", "cats": ["cond-mat.mtrl-sci"], "affil": "Université Grenoble Alpes, France"},
    {"id": "2302.00002v2", "title": "A Weil-Petersson geometric flow on Teichmüller space",
     "summary": "We introduce a geometric flow on Teichmüller space driven by the Weil-Petersson metric and prove short-time existence and convergence results for a class of initial data. "
                "Part of this work was carried out during a visit to Institut Fourier, France, and presented at the XVI Seminar on Geometric Analysis.", "authors": ["Louis Marchand", "Pavel R."], "cat": "math.DG", "cats": ["math.DG", "math.CV"], "affil": "Institut Fourier, France"},
    {"id": "2303.00003v1", "title": "Named entity recognition in nineteenth-century French newspaper archives",
     "summary": "We evaluate transformer models for named entity recognition on digitised French newspapers from the nineteenth century, where OCR errors and historical spelling reduce accuracy.",
     "authors": ["Marie D.", "Jean P."], "cat": "cs.CL", "cats": ["cs.CL", "cs.DL"], "affil": ""},
    {"id": "2304.00004v1", "title": "Sovereign debt and fiscal capacity in eighteenth-century France",
     "summary": "We assemble yearly data on royal revenue, expenditure and interest payments in France between 1740 and 1789 and estimate how war borrowing, including borrowing during the American War of "
                "Independence, raised the debt service of the monarchy of Louis XVI before the Revolution of 1789.", "authors": ["Helen V.", "Tomas B."], "cat": "econ.GN", "cats": ["econ.GN", "q-fin.GN"], "affil": ""},
    {"id": "2101.00001v2", "title": "Attention Is All You Need",
     "summary": "The dominant sequence transduction models are based on recurrent networks. We propose the Transformer, a model architecture based solely on attention mechanisms.",
     "authors": ["A. Author", "B. Author"], "cat": "cs.LG", "cats": ["cs.LG", "stat.ML"], "affil": ""},
    {"id": "2305.00005v1", "title": "Scalable graph transformers for molecular property prediction",
     "summary": "We propose a graph transformer for predicting molecular properties and evaluate it on standard benchmarks, with ablations of the positional encoding.", "authors": ["Wen L.", "Rosa M."],
     "cat": "cs.LG", "cats": ["cs.LG", "q-bio.BM"], "affil": ""},
]

# ---------------------------------------------------------------------------------------------------------------------------------------------- general web pages
WEB_PAGES: List[Dict[str, Any]] = [
    {"path": "/history/france-under-louis-xvi", "title": "France under Louis XVI: finances, society and the road to revolution", "published": "2026-03-10T09:00:00Z",
     "paras": ["Louis XVI became king of France in 1774 and inherited a treasury weakened by the wars of Louis XV. The monarchy depended on a tax system in which the nobility and clergy were largely exempt from "
               "the taille, so the burden fell on the peasantry and the towns.",
               "French intervention in the American War of Independence cost far more than the crown could raise in taxes, and the government borrowed heavily. By 1788 interest payments on the debt took roughly "
               "half of the crown's revenue.",
               "Reforming ministers such as Turgot and Necker tried to cut costs and widen the tax base, but the privileged orders and the Parlements resisted. When Calonne proposed a uniform land tax in 1787 the "
               "Assembly of Notables refused it, and the crown was forced to call the Estates General."],
     "content": "Louis XVI inherited a weakened treasury; nobility and clergy exempt from taxes; American war borrowing; Estates General called"},
    {"path": "/history/economy-before-1789", "title": "The French economy before 1789: debt, taxes and bread", "published": "2026-02-02T09:00:00Z",
     "paras": ["Before the Revolution the French economy was agrarian. Most people were peasants who paid the taille, the tithe and feudal dues, while privileged groups paid little direct tax.",
               "The royal debt rose because of the Seven Years' War and the American War of Independence. By the late 1780s the crown could not meet its debt payments without new taxes, and attempts to reform "
               "taxation were blocked.",
               "The harvest of 1788 was poor, and the price of bread rose sharply in the spring of 1789, which worsened the plight of wage earners in Paris."],
     "content": "French economy before 1789 debt taxes bread prices harvest"},
    {"path": "/history/why-the-revolution-happened", "title": "Why did the French Revolution happen? Causes and triggers", "published": "2026-01-15T09:00:00Z",
     "paras": ["Historians point to a combination of causes. Political: an absolute monarchy that could not reform itself. Economic: a fiscal crisis caused by war debt and an unfair tax system. Social: the "
               "resentment of the Third Estate towards privilege. Intellectual: Enlightenment ideas about rights and sovereignty.",
               "These long-term causes made a crisis likely; the immediate events of 1789 - the calling of the Estates General, the dispute over voting, the Tennis Court Oath and the storming of the Bastille - "
               "turned the crisis into a revolution."],
     "content": "causes of the French revolution political economic social intellectual immediate triggers"},
    {"path": "/history/louis-xviii-and-the-restoration", "title": "Louis XVIII and the Bourbon Restoration (1814-1824)", "published": "2026-01-20T09:00:00Z",
     "paras": ["Louis XVIII, the brother of Louis XVI, returned to France in 1814 after Napoleon's abdication. He ruled under the Charter of 1814, which set up a constitutional monarchy with two chambers.",
               "The Restoration regime had to deal with the cost of the war indemnity, the Hundred Days and the quarrel between ultra-royalists and moderates."],
     "content": "Louis XVIII Bourbon Restoration Charter of 1814 France"},
    {"path": "/design/louis-xvi-furniture", "title": "Louis XVI furniture: how to recognise the style", "published": "2025-11-02T09:00:00Z",
     "paras": ["Louis XVI furniture is recognised by its straight, tapered and fluted legs and by neoclassical ornament. Collectors look for marquetry and gilt bronze mounts on commodes and chairs in France."],
     "content": "Louis XVI furniture style France antiques"},
    {"path": "/travel/st-louis-weekend", "title": "A weekend in St. Louis: things to do", "published": "2026-05-01T09:00:00Z",
     "paras": ["St. Louis has a famous arch, a zoo and a lively food scene. Many visitors in France ask about direct flights, but the city is served mainly from US hubs."], "content": "St. Louis travel Missouri things to do"},
    {"path": "/shop/louis-vuitton-france", "title": "Louis Vuitton boutiques in France", "published": "2026-04-01T09:00:00Z",
     "paras": ["Louis Vuitton operates boutiques across France, including its flagship on the Champs-Elysees."], "content": "Louis Vuitton France shopping"},
]

# ---------------------------------------------------------------------------------------------------------------------------------------------- the uploaded French Revolution PDF (stand-in for "french rev.pdf")
HEADER = "A SHORT HISTORY OF THE FRENCH REVOLUTION"


def _wrap(text: str, width: int = 88) -> List[str]:
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines


def pdf_pages() -> List[str]:
    """Seven pages with a running header and a 'Page N' footer on every page, headings, and paragraphs that continue across a page break (the fragment problem)."""
    body = [
        ["Chapter 1. The Old Regime",
         "France before 1789 was a society of orders. The clergy formed the First Estate and the nobility the Second, and together they made up only a small part of the population. The Third Estate, "
         "which included peasants, artisans, merchants and lawyers, made up the great majority of the people of France and carried the greatest share of the burden of taxation. The privileged "
         "orders held offices, honours and exemptions that the Third Estate could not share, and this inequality was",
         ],
        ["felt more sharply each year as the bourgeoisie grew richer and better educated. Taxation was both heavy and unequal. The taille, a direct tax on land and income, fell on commoners, "
         "while the nobility and clergy were exempt from most direct taxes. Peasants also owed the tithe to the Church and feudal dues to their lords, and indirect taxes such as the gabelle on "
         "salt were collected by private tax farmers who kept a share of the proceeds.",
         "Chapter 2. The Financial Crisis",
         "The immediate cause of the Revolution was the bankruptcy of the monarchy. The wars of the eighteenth century, and above all French support for the American War of Independence, had been "
         "financed by borrowing. By 1788 interest payments on the debt absorbed about half of the royal budget, and the government could not borrow more."],
        ["Successive ministers tried to restore the finances. Turgot attempted to free the grain trade and to abolish the corvee, Necker relied on loans and published a famous account of the "
         "royal finances in 1781, and Calonne proposed in 1786 a uniform land tax to be paid by all landowners. When the Assembly of Notables met in 1787 it refused to accept the plan, and "
         "Brienne's own attempts at reform were rejected by the Parlement of Paris. The crown had no remaining way to raise money except by calling the Estates General, which had last met in 1614."],
        ["Chapter 3. Louis XVI and the Crown",
         "Louis XVI came to the throne in 1774 at the age of nineteen. Although the monarchy was in theory absolute, the king depended on ministers and was often unable to impose decisions on "
         "the court or the Parlements. He was well meaning but indecisive: he dismissed Turgot in 1776 and Necker in 1781 when their reforms provoked opposition. His queen, Marie Antoinette, "
         "was widely distrusted because she was Austrian, and her spending was blamed for the deficit.",
         "The king's authority also depended on the belief that he was the father of his people. The harvest failure of 1788 and the high price of bread in the spring of 1789 damaged that belief."],
        ["Chapter 4. The Ideas of the Enlightenment",
         "The writers of the Enlightenment, including Montesquieu, Voltaire and Rousseau, criticised arbitrary government and inherited privilege. Their ideas were discussed in salons, cafes and "
         "pamphlets by an educated public, and the success of the American colonists in establishing a republic showed that a new political order was possible. These ideas did not cause the "
         "Revolution by themselves, but they gave the Third Estate a language in which to demand equality before the law."],
        ["Chapter 5. The Events of 1789",
         "The Estates General met at Versailles in May 1789. A dispute over whether the three estates should vote by order or by head led the Third Estate to declare itself the National Assembly "
         "on 17 June. Three days later its members took the Tennis Court Oath not to separate until France had a constitution. On 14 July a Paris crowd stormed the Bastille, and in August the "
         "Assembly abolished feudal privileges and adopted the Declaration of the Rights of Man and of the Citizen."],
        ["Chapter 9. The Restoration",
         "After the fall of Napoleon the Bourbons returned. Louis XVIII, the younger brother of the executed Louis XVI, ruled from 1814 to 1824, apart from the Hundred Days of 1815. He accepted the "
         "Charter of 1814, a constitution that kept many of the changes of the Revolution. The Restoration was a period of compromise between the old order and the new."],
    ]
    pages: List[str] = []
    for n, chunks in enumerate(body, start=1):
        lines = [HEADER, ""]
        for c in chunks:
            lines += _wrap(c) + [""]
        lines += ["", f"Page {n}"]
        pages.append("\n".join(lines))
    return pages
