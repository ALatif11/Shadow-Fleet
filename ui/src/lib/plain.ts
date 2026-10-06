// Plain-English copy for everything the console shows. One place, so the wording stays consistent
// between the tooltips, the panel help strips and the guide screen.

export interface Term {
  term: string;
  short: string; // one line, shown on hover and in the glossary list
  long?: string; // the paragraph in the guide
}

export const GLOSSARY: Record<string, Term> = {
  cutoff: {
    term: "Cutoff (T)",
    short: "A date the system pretends is 'today'.",
    long:
      "Every score in this console was produced as if the date were the cutoff. The model only sees records " +
      "that had arrived by then. Stepping the cutoff moves the whole console to a different pretend-today, " +
      "which is how you check whether the system would have flagged a ship before anyone listed it.",
  },
  as_of: {
    term: "As-of",
    short: "The exact moment the map and event log are showing.",
    long:
      "The scrubber at the bottom sets it. Drag it back and events disappear, because they had not been " +
      "observed yet. It cannot go past the cutoff unless you turn on hindsight.",
  },
  feature_window: {
    term: "Feature window",
    short: "The 180 days before the cutoff that the score is calculated from.",
    long: "Behaviour older than 180 days still shows on the map, dimmer, but does not feed the score.",
  },
  horizon: {
    term: "Horizon",
    short: "The 182 days after the cutoff where a designation counts as a hit.",
    long:
      "A ship counts as a correct flag if OFAC, the EU or the UK listed it within 182 days of the cutoff. " +
      "Listed later than that and it counts as a miss, on purpose: a warning two years early is not a warning.",
  },
  hindsight: {
    term: "Hindsight",
    short: "Reveals what happened after the cutoff. Off by default.",
    long:
      "With hindsight off you see exactly what an analyst would have seen on the cutoff date. Turn it on and " +
      "outcomes, designation dates and future events appear, and the scrubber is allowed past the cutoff. " +
      "It is for checking results, not for scoring.",
  },
  score: {
    term: "Score",
    short: "0 to 1. How strongly this ship matches the evasion pattern at this cutoff.",
    long: "Not a probability of guilt. It is a ranking number: the list is sorted by it, nothing more.",
  },
  rank_delta: {
    term: "Δ (rank change)",
    short: "How far the ship moved up or down since the previous cutoff.",
    long: "▲ means it climbed toward the top of the watchlist this month. NEW means it was not in the list before.",
  },
  population: {
    term: "POP (population)",
    short: "How many tankers were eligible at this cutoff.",
    long:
      "Tankers seen in Danish waters during the feature window, minus every hull already sanctioned by OFAC, " +
      "the EU or the UK on that date. Already-listed ships are excluded: finding them proves nothing.",
  },
  positives: {
    term: "POS (positives)",
    short: "How many of those ships were actually listed within the horizon.",
    long: "The number of correct answers available at this cutoff. If it is small, precision moves in big jumps.",
  },
  precision_at: {
    term: "P@50 (precision at 50)",
    short: "Of the top 50 ships, the share that really were listed within the horizon.",
    long:
      "P@50 of 12 percent means 6 of the top 50 were listed inside 182 days. This is the headline number: " +
      "an analyst can review 50 ships a month, so being right near the top of the list is what matters.",
  },
  precision_b1: {
    term: "P@50·B1",
    short: "The same, but only among ships that already called at a Russian port.",
    long:
      "The hard version of the test. Flagging Russia-trade tankers is easy; the question is whether the model " +
      "can rank within that group. If P@50 is high but P@50·B1 is not, the model just learned 'trades with Russia'.",
  },
  recall_at: {
    term: "R@100 (recall at 100)",
    short: "Of every ship later listed, the share that appeared in the top 100.",
    long: "Precision asks how clean the list is. Recall asks how much it missed.",
  },
  pr_auc: {
    term: "PR-AUC",
    short: "One number for the whole ranking, 0 to 1. Higher is better.",
    long:
      "Summarises precision across every cut-off depth instead of just the top 50. Useful for comparing models, " +
      "less useful for deciding what to review.",
  },
  b1: {
    term: "B1",
    short: "Badge meaning the ship visited a Russian port or declared one as its destination.",
    long: "B1 is also the simplest rules baseline. Any model has to beat 'flag everything that trades with Russia'.",
  },
  label_set: {
    term: "Label set",
    short: "Whose sanctions list counts as the right answer.",
    long:
      "OFAC ∪ EU ∪ UK counts a designation by any of the three. OFAC only is the stricter check. Either way a " +
      "hull already on any of the three lists is removed from the population.",
  },
  model: {
    term: "Model",
    short: "Which scorer ranked the list.",
    long:
      "B2 Rules is a hand-weighted checklist written before any labels were loaded. LightGBM and LogReg are " +
      "trained models. IsoForest looks for odd behaviour without using labels at all. The rules baseline is the " +
      "one to beat; if it wins, that is the finding.",
  },
  supervised: {
    term: "Supervised",
    short: "Whether trained models can be scored at this cutoff.",
    long:
      "A trained model may only learn from cutoffs whose 182-day horizon had already closed. At the earliest " +
      "cutoffs nothing had, so only rules and unsupervised scores exist there.",
  },
  hull_id: {
    term: "Hull ID",
    short: "The console's permanent name for one physical ship.",
    long:
      "Ships change name, flag and radio ID to break the trail, so identity is rebuilt from the hull's IMO number " +
      "where possible. fixture: ids are synthetic demo hulls.",
  },
  imo: {
    term: "IMO number",
    short: "A seven-digit number welded to the hull for life.",
    long: "Unlike name, flag and MMSI, it does not change when a ship is sold or reflagged.",
  },
  mmsi: {
    term: "MMSI",
    short: "The radio ID a ship broadcasts. Easy to change.",
    long: "A hull quietly swapping MMSI while keeping its IMO is one of the churn signals the model uses.",
  },
  flag: {
    term: "Flag",
    short: "The country the ship is registered in, from its radio ID prefix.",
  },
  dwt: { term: "DWT", short: "Deadweight tonnage: how much cargo, fuel and stores the ship can carry." },
  sog: { term: "SOG", short: "Speed over ground, in knots." },
  draught: {
    term: "Draught",
    short: "How deep the hull sits. Loaded ships sit deep, empty ones ride high.",
    long: "A draught change with no port call in between suggests cargo moved at sea.",
  },
  ais: {
    term: "AIS",
    short: "The radio position broadcast every ship transmits.",
    long: "Cooperative data: it only exists while the ship chooses to transmit. Turning it off is itself a signal.",
  },
  dma: {
    term: "DMA",
    short: "Danish Maritime Authority, source of the raw tracks in Danish waters.",
    long: "Shore stations around the Danish straits, the exit every Baltic tanker has to pass through.",
  },
  gfw: {
    term: "GFW",
    short: "Global Fishing Watch, source of the worldwide events.",
    long: "Their models detect gaps, meetings at sea and port calls globally, outside the Danish coverage.",
  },
  self_built: {
    term: "Self-built",
    short: "Detections this project computes itself from raw tracks.",
    long: "Ship-to-ship candidates, anchorage loitering, draught anomalies. The layer the project owns.",
  },
  driver: {
    term: "Why flagged",
    short: "The pieces of behaviour that pushed the score up, largest first.",
    long: "Bars to the right raised the score, bars to the left lowered it. The number is the feature's raw value.",
  },
  designation: {
    term: "Designation",
    short: "The date an authority formally listed the ship.",
    long: "OFAC (US Treasury), EU (Annex XLII to Reg 833/2014), UK (FCDO). Hindsight only.",
  },
  lead_time: {
    term: "Lead time",
    short: "How many weeks before the designation the ship first entered the top 50.",
  },
  synthetic: {
    term: "SYNTHETIC",
    short: "Every hull, event and number on screen is invented.",
    long:
      "The pipeline has not produced results yet. The console runs on a generated demo bundle so the interface " +
      "can be built and reviewed. Nothing here is a finding.",
  },
};

export const EVENT_HELP: Record<string, string> = {
  gap: "The ship stopped transmitting its position for hours or days, then reappeared somewhere else.",
  encounter: "Two ships sat next to each other at sea, slowly, long enough to transfer cargo.",
  loitering: "The ship idled at sea away from any port.",
  port_visit: "The ship called at a port. Russian ports are what the B1 rule keys on.",
  sts_candidate: "Two tanker hulls within 500 m, both nearly stopped, for over two hours, outside a port.",
  anchorage_loitering: "The ship sat in an anchorage for a long stretch, typically the Skagen anchorage.",
  draught_inconsistency: "The ship's draught changed with no port call in between, so cargo likely moved at sea.",
  identity_change: "The ship changed its name, flag or radio ID.",
  spoof_day: "The broadcast position jumped further than the ship could physically travel.",
};

export const PANEL_HELP: Record<string, string> = {
  watchlist:
    "Tankers ranked by how strongly they match the evasion pattern on the pretend-today date, most suspicious first. " +
    "The scorecard above the list grades the ranking itself, not any one ship.",
  map:
    "Where the selected ship has been, and what happened to it. The bright track is inside the 180-day scoring " +
    "window; dimmer track is older. Coloured marks are events, and the legend switches each type on and off.",
  dossier:
    "Everything known about the selected ship as of the scrubber date: who it currently claims to be, what pushed " +
    "its score up, how its rank moved, and every event in time order.",
  timeline:
    "The ship's whole history on one line. Press play to sail it, or drag anywhere to move the as-of moment by hand. The shaded block is the " +
    "180-day scoring window; the hatched area after the cutoff is the future, locked until you turn on hindsight.",
  brief: "A written summary of why this ship was flagged, generated by the model running on your own machine.",
  chat: "Ask the local model about the selected ship. It is given that ship's evidence and nothing else.",
};

export const HOW_TO: { step: string; body: string }[] = [
  {
    step: "1 · Pick a pretend-today",
    body:
      "PRETEND TODAY at the top is the date the console pretends it is. Everything on screen is what was " +
      "knowable then. Step it with the arrows or the [ and ] keys, and watch the list re-rank.",
  },
  {
    step: "2 · Read the watchlist",
    body:
      "The left panel ranks every eligible tanker, most suspicious first. Rank 01 is the ship the model would " +
      "have put in front of an analyst that month. Arrow up and down to move through it.",
  },
  {
    step: "3 · Ask why",
    body:
      "Click a ship and its file opens in the dock on the right. WHY IT WAS FLAGGED lists the behaviour behind " +
      "the score, biggest reason first. Hover almost anything for a plain-English note.",
  },
  {
    step: "4 · Check the evidence",
    body:
      "The map and the event log show the actual records behind those numbers: where it went dark, who it met, " +
      "which ports it called at. Click an event to fly the map to it. BRIEF and ASK let your local model " +
      "explain the case; its citations are clickable.",
  },
  {
    step: "5 · Play it back",
    body:
      "Press play (or space) and the ship sails its own track while the clock runs: incidents ping on the map as " +
      "they happen, and nothing appears before it was observed. Pick a speed, and turn FOLLOW off if you would " +
      "rather keep the whole theatre in view. Long stretches with no coverage are skipped automatically. " +
      "Dragging the scrubber does the same thing by hand.",
  },
  {
    step: "6 · Then, and only then, look at the answer",
    body:
      "Press H for hindsight. Outcomes appear: who was actually designated, when, and how many weeks of warning " +
      "the model would have given. Turn it back off before judging any other ship.",
  },
];
