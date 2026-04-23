# Semantic Scholar Query Generation Guide for RoboScout

## Purpose

This guide defines how to craft high-quality search queries for RoboScout, which searches Semantic Scholar publications to find relevant researchers for corporate R&D partnering requests.

---

## Query Crafting Rules

### DO: Use specific compounds, materials, technologies, systems
Instead of broad terms from the RFP, identify specific materials, compounds, or technologies that could fulfill the requirements.

**Example:** If a request seeks barrier technologies for paper packaging:
- BAD: "barrier technologies for packaging"
- GOOD: "sprayable polybutylene succinate blends for paper packaging"
- GOOD: "bio-based nanocellulose coatings food packaging"

### DO: Use conjunctions and prepositions to narrow results
Words like "and," "in," "or," and "for" can significantly reduce result counts.

**Examples showing the effect:**
- "Oxygen barrier food packaging" → ~7,430 results
- "Oxygen barrier for food packaging" → ~1,160 results
- "New moisture barrier or oxygen barrier for food packaging" → ~52 results

### DO: Add field-specific context
Include terms that anchor the query in the relevant domain.

**Example:** "stiff moisture barrier" might return catalysis or solar cell papers. Adding "in food packaging" makes it relevant.

### DON'T: Use quotation marks
Quotation marks cause poor or irrelevant results on Semantic Scholar with RoboScout.

### DON'T: Use boolean operators (AND, OR, NOT)
These don't work properly with RoboScout. Use natural language instead.

### DON'T: Use negation prefixes (non-, un-)
"Non-peroxide bleaching" will pull in papers about peroxide. Instead, query for the specific alternative: "enzymatic bleaching for textiles"

### DON'T: Use "alternatives to X"
"Aluminum foil alternatives for packaging" returns papers about aluminum foil. Instead, query for specific replacement materials: "polylactic acid thermoplastic food packaging"

### DON'T: Use generic filler words
Avoid: "novel," "new," "method," "data," "significant," "treatment" — unless they genuinely narrow results.

---

## Query Classification

| Category | Result Count | How Many to Submit |
|----------|-------------|-------------------|
| General | 1,001–3,000 | 1-2 queries max |
| Moderate | 500–1,000 | 2-4 queries |
| Specific | < 500 | As many as needed |
| Highly Specific | < 100 | As many as needed |

**IMPORTANT:** Never submit queries returning >3,000 results. They slow down RoboScout and produce low-quality results.

---

## Coverage Requirements

**Primary goal: Cover every SOI**, whether with Specific or Highly Specific queries.

### Queries per SOI

- **Minimum:** 1 query per SOI (no SOI should be left uncovered)
- **Typical:** 1–2 queries per SOI
- **Maximum:** 3–5 queries per SOI, ONLY if they target significantly different solution directions

### Specificity balance per SOI

Each SOI should have **at least one Specific query** (100–500 results) AND **at least one Highly Specific query** (<100 results). Together, the queries for a given SOI should contribute **a minimum of 100 total results**.

- The Highly Specific query finds the most targeted researchers — the closest matches
- The Specific query casts a wider net to ensure we don't miss relevant work outside the narrow framing
- The 100-result minimum ensures there's enough volume to produce a meaningful candidate pool

### When more queries per SOI are justified

Some SOIs are broad enough to hide multiple distinct solution directions — different mechanisms, materials, or use contexts. In those cases, additional queries expand coverage meaningfully.

**Example:** For an SOI like "Botanical or herbal-based remedies with proven efficacy," these queries are **bad** (semantic equivalents that retrieve the same papers):
- "botanical remedies cough"
- "herbal remedies cough"

Instead, split by distinct mechanism or specific existing solution:
- "iota-carrageenan antiviral barrier throat spray"
- "Pelargonium sidoides root extract acute bronchitis immune modulation"
- "mucoadhesive throat film soothing botanical extract"

That's the kind of split where a 4th or 5th query per SOI actually expands coverage.

### When fewer queries are better

If queries overlap heavily, they're inefficient — they retrieve the same papers, spending compute and time without expanding coverage. Prefer fewer, more distinct queries over many overlapping ones.

### Additional SOIs

- SOIs discovered through domain research should also be covered
- Aim for comprehensive coverage across all relevant technologies and approaches

---

## Quality Threshold

Before submitting, validate that >60% (ideally 80%+) of the first page of Semantic Scholar results are relevant. Discard queries where >40% of first-page results are irrelevant.

---

## Real Examples from Human Scouts

These are actual query sets written by experienced scouts. Study the patterns: specific compounds/organisms, field context, mix of specificity levels.

### Example 1: Precision Fermentation for Ingredients (Request 1332)

The request sought researchers in precision fermentation for producing sweeteners, pigments, and antioxidants.

Scout-written queries (result count; relevance scores):
- precision fermentation sweeteners (1,740; 10/10, 8/10) — General, anchors on the core technology + target
- expression systems for sweeteners production (41; 10/10, 10/10) — Highly specific, shifts to the underlying method
- pigment production by fermentation (1,450; 10/10, 10/10) — General, covers a different SOI (pigments)
- antioxidants production by fermentation (3,190; 10/10, 8/10) — Borderline too broad but high relevance
- antioxidant singlet oxygen quenchers produced by fermentation (131; 8/10, 7/10) — Specific, narrows to a mechanism
- free radical scavengers precision fermentation (68; 10/10, 9/10) — Highly specific, different antioxidant mechanism

**What makes this set good:**
- Covers all three SOIs (sweeteners, pigments, antioxidants)
- Mixes specificity levels (2 general, 1 specific, 1 highly specific)
- Uses specific scientific mechanisms (singlet oxygen quenchers, free radical scavengers) not just broad categories
- "production by fermentation" pattern anchors domain without being overly narrow

### Example 2: Improving Quality of Recycled Plastics (Request 1361)

The request sought researchers working on purification, color correction, and stabilization of recycled plastics (PET and HDPE).

Scout-written queries (result count; relevance scores):
- purification of polyethylene terephthalate (135; 7/10, 7/10) — Specific, direct approach
- stilbene brightener recycled plastic (10; 7/10) — Highly specific, names a specific chemical class
- color-corrected transparent recycled plastic (180; 8/10, 7/10) — Specific, describes desired outcome
- benzoxazole optical brightener PET (26; 8/10) — Highly specific, names exact compound class + polymer
- rHDPE phosphite antioxidant discoloration (21; 9/10, 7/10) — Highly specific, combines polymer abbreviation + additive + problem
- recycled PET melt decontamination additive (59; 7/10) — Highly specific, process-specific
- chemical depolymerization of recycled plastic (309; 8/10, 10/10) — Specific, different approach (chemical recycling)
- recycled PET molecular stabilizer (531; 8/10, 8/10) — Moderate, broader stabilization query
- recycled HDPE molecular stabilizer (204; 8/10, 8/10) — Specific, same approach different polymer
- compatibilizers for recycled plastics (237; 8/10, 7/10) — Specific, different additive class

**What makes this set good:**
- Names specific chemicals (stilbene, benzoxazole, phosphite) not just broad categories
- Uses polymer abbreviations (PET, HDPE, rHDPE) that researchers actually use in papers
- Covers multiple approaches: purification, optical brighteners, antioxidants, depolymerization, stabilizers, compatibilizers
- Heavy on highly specific queries (6 out of 10) — appropriate for a technical chemistry request
- Pairs polymer + additive + application consistently

### Example 3: Edible Oil Refining Process Optimization (Request 1254)

The request sought researchers working on improving efficiency, yield, and sustainability across the edible oil refining process — covering degumming, neutralization, bleaching, dewaxing/winterization, fractionation, and deodorization.

Scout-written queries (result count; relevance scores):
- cost efficiency edible oil degumming (240; 7/10, 6/10) — Specific, anchors on economics + process step
- enzymes degumming edible oil (1,100; 10/10, 7/10) — General, specific technology (enzymatic) for degumming
- edible oil phospholipid removal (740; 9/10, 9/10) — Moderate, names the actual substance being removed in degumming
- yield improvement neutralization process vegetable oil refining (462; 9/10, 9/10) — Specific, combines goal + process step + domain
- bleaching earth filter aid reduction edible oil (37; 7/10, 5/10) — Highly specific, names exact materials used in bleaching
- activated clay bleaching waste minimization edible oil (102; 7/10, 6/10) — Specific, names the adsorbent + sustainability angle
- filtration optimization dewaxing edible oil (18; 6/10) — Highly specific, process engineering focus
- optimization winterization edible oil refining (75; 10/10, 9/10) — Highly specific, direct winterization process query
- membrane fractionation edible oil refining (115; 10/10, 8/10) — Specific, names a specific separation technology
- low temperature steam stripping deodorization edible oil (153; 10/10, 9/10) — Specific, describes exact process conditions for deodorization
- vacuum system molecular distillation for edible oil (45; 9/10, 7/10) — Highly specific, names equipment type + technique
- thermal degradation reduction deodorization edible oil (858; 10/10, 10/10) — Moderate, problem-oriented (reducing degradation during deodorization)
- process intensification edible oil refining (628; 9/10, 7/10) — Moderate, broader process engineering approach
- membrane-based centrifugal separation dewaxing edible oil (27; 9/10, 8/10) — Highly specific, combines two separation methods
- crystal management fractionation edible oil refining (218; 8/10, 5/10) — Specific, names the physical mechanism in fractionation
- heat integration stripping food oils refining (105; 9/10, 6/10) — Specific, energy efficiency angle on deodorization
- high adsorption bleaching edible oil (214; 10/10, 8/10) — Specific, performance-oriented bleaching query
- process analytics yield prediction edible oil refining (310; 8/10, 7/10) — Specific, data/analytics angle on process optimization
- high speed vertical separators edible oil refining (50; 10/10, 10/10) — Highly specific, names exact equipment type

**What makes this set good:**
- Comprehensive coverage of all 6 refining stages (degumming, neutralization, bleaching, dewaxing/winterization, fractionation, deodorization)
- 19 queries — appropriate for a broad, multi-stage process request (more SOIs = more queries needed)
- Names specific materials and equipment (bleaching earth, activated clay, membrane, vertical separators) rather than generic process terms
- Uses multiple angles per SOI: e.g., deodorization covered by steam stripping, thermal degradation, heat integration, vacuum/molecular distillation
- Mixes problem-oriented queries ("thermal degradation reduction") with technology-oriented queries ("membrane fractionation")
- Includes cross-cutting queries (process intensification, process analytics) that span multiple stages
- "edible oil refining" suffix consistently anchors the domain context
- Heavy on specific and highly specific queries (6 highly specific, 8 specific) — appropriate for a process engineering request where each stage has distinct technologies

---

## Additional Query Set Examples

The following are validated query sets from real scouting engagements. Study the vocabulary choices, specificity patterns, and how each set covers its topic from multiple angles.

### Example 4: Reduction of Trivalent Chromium in Steel-Making By-Product Slag

- biomass adsorption of chromium steel slag
- removal of chromium from oxide and silicate waste
- electrochemical extraction of chromium from slag
- hydrometallurgical treatment chromium removal from steel slag
- chemical treatment of steel slag
- trivalent chromium chemical treatment of steel slag
- selective leaching of chromium steel slag
- chromium stabilization high-temperature sintering
- crystalline phase transformation chromium ld slag
- organic acid chelation for chromium removal steel
- humic immobilize chromium in steel slag
- bioleaching chromium steel slag

**Pattern notes:** Covers multiple extraction/removal strategies (chemical, electrochemical, hydrometallurgical, bioleaching, adsorption). Uses specific mechanisms (chelation, sintering, phase transformation, selective leaching). Names specific reagent classes (organic acid, humic substances, biomass).

### Example 5: Non-Adhesive Coatings and Materials for Foam Curing

- nonstick plasma sprayed ceramic coating in manufacturing
- dlc coating nonstick temperature
- superhydrophobic low surface energy coating for metal
- fluoropolymer infused ceramic coatings
- titanium nitride coating for mold applications
- ceramic coating nonstick hydrophobic
- plasma deposited silane coating for metal
- silicon nitride coating nonstick mold
- siloxane based coating mold manufacturing
- graphene coatings nonstick superhydrophobic

**Pattern notes:** Names specific coating materials (DLC, titanium nitride, silicon nitride, fluoropolymer, siloxane, graphene). Combines material + function (nonstick) + application (mold, manufacturing). Explores deposition methods (plasma sprayed, plasma deposited).

### Example 6: Natural Food Preservation Against Spoilage LAB

- natural antimicrobial agents against spoilage leuconostoc mesenteroides
- peptide antimicrobials against lactobacillus plantarum in food
- natural preservatives for food spoilage prevention
- bacteriocins for natural food preservation
- enzyme based inhibitors for lactic acid bacteria spoilage
- essential oils for lactic acid bacteria spoilage
- natural compounds to inhibit lactic acid bacteria in food
- control of spoilage bacteria in ready to eat meats
- food preservation against lab spoilage
- lactobacillus buchneri spoilage in food
- lactobacillus plantarum inhibition in food
- food preservation against lactobacillus buchneri spoilage

**Pattern notes:** Uses genus+species names (leuconostoc mesenteroides, lactobacillus plantarum, lactobacillus buchneri) alongside group terms (lactic acid bacteria, LAB). Covers multiple antimicrobial classes (bacteriocins, essential oils, peptides, enzymes). Includes application context (ready to eat meats).

### Example 7: Biofilm Coatings for Preservation of Fresh Produce

- biodegradable coatings for extending shelf life
- natural biofilm solutions for food preservation
- sustainable biofilm technologies for fresh produce
- edible coatings for extending shelf life of fresh produce
- antimicrobial biofilm solutions for produce preservation
- polysaccharide based films for produce shelf life extension
- biofilm coatings for food preservation
- chitosan biofilm coatings for fresh produce shelf life extension
- plant extract based biofilm coatings for fresh produce
- microbial biofilm coatings for preserving leafy greens
- natural biofilm coatings for reducing spoilage in fruits
- eco friendly biofilm solutions for preventing produce decay
- edible biofilms for produce preservation

**Pattern notes:** Names specific materials (chitosan, polysaccharide, plant extract). Targets specific produce types (leafy greens, fruits). Varies between broader (food preservation) and narrower (fresh produce shelf life extension) framing.

### Example 8: Advancing Energy Storage and Transfer for Heavy-Duty Electric Vehicles

- hybrid capacitors for extended power storage
- lithium sulfur batteries degradation in electric vehicles
- metal air batteries for lightweight energy storage
- electric vehicle inductive resonant coupling
- wireless power transfer using magnetoelectric materials evs
- energy dense solid state batteries electric vehicle
- magnetoelectric wireless charging solutions for electric vehicles
- advanced thermal management in batteries
- advanced thermal management in batteries for electric vehicle
- compact vanadium redox flow batteries mobile applications electric vehicle
- magnetoelectric wireless charging solutions for industrial electric vehicles
- lithium sulfur battery capacity degradation in electric vehicles

**Pattern notes:** Covers distinct technology families (Li-S, solid state, metal-air, vanadium redox flow, hybrid capacitors). Includes charging infrastructure (inductive coupling, magnetoelectric wireless). Addresses engineering challenges (thermal management, degradation). Appends "electric vehicle" context to anchor the domain.

### Example 9: Drug Delivery Systems That Penetrate the Blood-Brain Barrier

- targeted drug delivery system to the brain
- targeted small molecule delivery to the brain
- oligonucleotide drug delivery to the brain
- transferrin receptor for brain delivery
- antibody oligonucleotide delivery to the brain
- cd98hc for brain targeting

**Pattern notes:** Short, focused set. Names specific receptor targets (transferrin receptor, cd98hc). Covers different payload types (small molecule, oligonucleotide, antibody conjugates). "To the brain" suffix anchors domain context.

### Example 10: Sustainable Packaging Solutions for SB54 Compliance

- biodegradable polymer film for packaging
- structural integrity biopolymer film packaging
- fiberbased packaging recyclable renewable
- barrier properties of pla for packaging
- pla modifications for improved strength packaging
- biobased pha film packaging
- starch pbat composites for sustainable packaging
- starch blend flexible packaging
- tri layer compostable packaging film

**Pattern notes:** Names specific polymers (PLA, PHA, PBAT) and blends (starch-PBAT). Covers different performance aspects (barrier, structural integrity, strength, flexibility). Includes multi-layer structures (tri layer compostable). Uses material abbreviations researchers actually publish with.

### Example 11: Exploring Protease Inhibitors as Therapeutic Agents

- inhibiting proteases extracellularly for proteolytic disorders
- extracellular protease inhibition in disease treatment animal studies
- role of proteases in neurodegenerative diseases pathogenesis
- inhibiting extracellular proteases for treating autoimmune conditions
- extracellular single protease inhibitors for neurodegenerative diseases
- single or dual extracellular protease inhibition disease treatment
- dual extracellular protease inhibition disease treatment neuro animal studies
- protease inhibitors in autoimmune and neurodegenerative diseases
- disease therapeutic outcomes from single or dual protease inhibition

**Pattern notes:** Systematically varies single vs dual inhibition approaches. Covers different disease contexts (neurodegenerative, autoimmune). Anchors on "extracellular" to distinguish from intracellular protease inhibition. Includes evidence type (animal studies).

### Example 12: Formation and Stability of Co-Assembled Bioactive Compounds

- predicted interaction motifs and binding energies
- density functional theory for biding energy prediction hydrogen bond
- molecular dynamics model predicting intermolecular structure formation hydrogen bond
- solid-state characterization by powder xray diffraction and solid state nmr
- ftir hydrogen bond vibrational shifts nmr ingredients
- solubility prediction powder xray diffraction solid state ftir
- differential scanning calorimetry glass transition recrystallization behavior ingredients
- dissolution testing apparent solubility precipitation tendency

**Pattern notes:** Organized around characterization techniques (DFT, molecular dynamics, XRD, NMR, FTIR, DSC). Names specific analytical methods and measurable properties (binding energies, vibrational shifts, glass transition, recrystallization). Bridges computational prediction and experimental characterization.

### Example 13: Food Innovations Supporting Physical and Mental Energy

- slowly digestible starches sustained energy
- food nutrition long lasting physical endurance
- supplement for balanced energy levels
- nutrient for cognitive energy
- novel food ingredient for energy boost
- short-chain fatty acids and perceived energy
- personalized nutrition for daily energy outcomes
- triglycerides use in dietary energy
- protein fat combinations for stable energy
- postprandial energy in healthy adults
- adaptogens for fatigue
- ingredients for mental energy
- microbiome effects on daily energy
- mitigating postprandial fatigue through supplementation

**Pattern notes:** Covers multiple energy mechanisms (digestive, cognitive, microbiome, macronutrient combinations). Names specific compound classes (SCFAs, triglycerides, adaptogens, slowly digestible starches). Includes clinical framing (postprandial, healthy adults). Distinguishes physical vs mental energy.

### Example 14: Boosting Nitrogen Use Efficiency

- biological nitrification inhibition root exudates nitrogen use efficiency
- brachialactone biological nitrification inhibition brachiaria nitrogen use efficiency
- sorgoleone sorghum nitrification suppression nitrogen use efficiency
- benzoxazinoids maize mboa nitrification nitrogen use efficiency
- soil urease inhibition agriculture catechin urea hydrolysis
- isothiocyanate nitrification inhibition ammonia oxidizer
- allelopathy nitrification suppression tropical grasses nitrogen use efficiency
- biodegradable urease inhibitor phosphoramidate agriculture nitrogen use efficiency
- urease inhibitors plant inspired phenolic agriculture
- natural nitrification inhibitor agriculture nitrogen use efficiency
- diazotrophic endophytes cereals azospirillum nitrogen use efficiency
- gluconacetobacter diazotroph inoculation nitrogen use efficiency
- synthetic microbial consortia for nitrogen use efficiency soil
- nosz clade ii inoculant nitrous oxide reduction nitrogen use efficiency agriculture
- microbiome nitrogen fixation endophyte colonization nue
- peptide inhibitors urease agriculture nitrogen use efficiency
- ammonia monooxygenase inhibition nitrification suppression soil
- biogenic inhibitors nitrifying bacteria ammonia oxidizer enzyme inhibition agriculture
- biodegradable coating urea release kinetics nitrogen use efficiency
- chitosan coated urea controlled release nitrogen use efficiency
- polylactic acid pla coated urea nitrogen use efficiency agriculture
- enzyme responsive microcapsules urease triggered release soil
- ph responsive hydrogel urea fertilizer smart release biodegradable

**Pattern notes:** Exceptionally detailed set covering 3 distinct strategies: (1) natural nitrification inhibitors — names specific compounds (brachialactone, sorgoleone, benzoxazinoids, isothiocyanate, catechin), (2) biological nitrogen fixation — names specific organisms (azospirillum, gluconacetobacter), and (3) controlled-release fertilizer coatings — names specific materials (chitosan, PLA, hydrogel). Uses enzyme targets (urease, ammonia monooxygenase). "nitrogen use efficiency" suffix anchors domain.

### Example 15: Ultra-Thin Coatings for Low-Friction Applications

- silicon doped diamond like carbon low friction humid pecvd thin film
- diamond like carbon si o doped low friction submicron deposition temperature
- wc c coating low friction pvd submicron adhesion chromium nitride
- topcoat adhesion on chromium nitride thin film
- zwitterionic polymer brush low friction stainless steel
- pmpc brush coating low friction water lubrication
- sulfobetaine methacrylate brush adhesion metal oxide
- polymer brush thin film stainless steel adhesion
- polydopamine primer polymer brush adhesion
- plasma polymerized hmdso thin film low friction
- organosilicon thin film pecvd low friction adhesion metal
- siloxane thin film coating tribology stainless steel
- sol gel organosilica thin film stainless steel adhesion
- molybdenum disulfide sputtered thin film low friction chromium
- tungsten disulfide sputtered thin film low friction
- boron carbon nitride thin film low friction pecvd
- hexagonal boron nitride sputtered thin film low friction stainless steel
- parylene thin film tribology friction coefficient chromium adhesion
- silane coupling agent adhesion dlc coating primer
- silane coupling agent adhesion chromium coating primer
- polydopamine adhesion layer chromium nitride dlc coating

**Pattern notes:** Highly technical materials science set. Names specific coating chemistries (DLC, WC/C, MoS2, WS2, h-BN, BCN, HMDSO, parylene). Names specific deposition methods (PECVD, PVD, sputtering, sol-gel, plasma polymerization). Covers adhesion promotion strategies (polydopamine, silane coupling agent). Combines material + method + property (friction) + substrate (stainless steel, chromium nitride).

### Example 16: Long-Term Breeding Effects on Maize Adaptation to Climate Change

- paleogenomics crop adaptation corn
- comparative crop analysis maize genotype in climate change
- historical trends in hybrid corn
- root architecture changes in maize hybrids over time
- anthesis silking interval corn hybrids
- evolutionary tradeoffs in maize crop improvement
- pleiotropy under long term selection in maize
- transgenerational epigenetics in crops
- gene expression changes induced by environment in maize
- rhizosphere effects from breeding corn
- predicting future performance maize crops
- climate impact in phenotype crops
- emergent traits in maize climate adaptation

**Pattern notes:** Covers multiple scales: molecular (epigenetics, gene expression, pleiotropy), organismal (root architecture, anthesis silking interval), and population (breeding, selection). Uses both "corn" and "maize" since papers use both terms. Includes temporal framing (historical trends, long term, over time, future).

### Example 17: Promoter Variant Impact on Gene Expression in Plants

- single-nucleotide editing promoter reporter assay plant
- promoter variant gene expression reporter assay germplasm plant
- promoter snp functional analysis in plant gus
- high-throughput promoter region editing transient expression plant
- prime editing plant promoter region beta-glucuronidase
- in silico plant promoter snps mutant
- base editing of plant promoter region gfp
- single nucleotide polymorphism in plant promoter region gus
- promoter snp functional analysis in plant rna-seq

**Pattern notes:** Names specific gene editing techniques (prime editing, base editing, single-nucleotide editing). Names specific reporter systems (GUS/beta-glucuronidase, GFP). Covers both experimental (reporter assay, transient expression) and computational (in silico) approaches. Includes readout methods (rna-seq).

### Example 18: Insecticides Targeting Brown Planthopper in Rice Crops

- nilaparvata lugens novel insecticide
- nilaparvata lugens neonicotinoid analog
- nilaparvata lugens pyrethroids analog
- novel delivery strategies for insecticides
- nilaparvata lugens rice pest control
- target site mutation nilaparvata lugens
- chemical synergists detoxification inhibitors brown planthopper
- rnai for brown planthopper control
- dsrna pest management nilaparvata lugens
- insect growth regulators brown planthopper
- chitin synthesis inhibitors nilaparvata lugens
- nano carrier insecticide delivery agriculture

**Pattern notes:** Uses scientific name (nilaparvata lugens) alongside common name (brown planthopper). Covers traditional chemistry (neonicotinoid, pyrethroids), biologics (RNAi, dsRNA), growth regulators, and delivery technology (nano carrier). Addresses resistance (target site mutation, detoxification inhibitors).

### Example 19: Biodegradable Thermoplastic Resin for Injection Molding

- biodegradable injection moldable resin
- biodegradable pha composites for injection molding
- bio-based polyester composites for injection molding
- low density thermoplastic starch for injection molding
- low density biodegradable polymers for injection molding
- foamed resins for food packaging
- physical foaming resin for injection molding
- polyhydroxyalkanoate composites for injection molding
- low density polybutylene succinate
- low density polybutylene adipate terephthalate
- low density biodegradable polymer composites for food packaging
- low density biodegradable polymer composites for injection molding
- low density thermoplastic starch for food packaging

**Pattern notes:** Names specific biodegradable polymers (PHA, PBS, PBAT, thermoplastic starch). Combines material + processing method (injection molding) + property (low density). Includes foaming as a density-reduction strategy. Covers both processing and end-use (food packaging).

### Example 20: Elevating Lactose and Derivatives Through Innovative Processes

- functional peptides from lactose fermentation
- succinic acid production from lactose whey permeate
- butanediol from lactose whey permeate
- triethyl citrate plasticizer from lactose whey
- citrem emulsifier from lactose
- lactobionic acid production from lactose
- lactose-derived nutraceuticals whey
- galactosyl derivatives from lactose permeate
- lactulose from lactose whey permeate
- transglycosylation novel oligosaccharide-based hydrogels from lactose
- lactose-based maillard reaction products whey
- polylactic acid from lactose whey permeate

**Pattern notes:** Names specific value-added products (succinic acid, butanediol, triethyl citrate, lactobionic acid, lactulose, citrem, galactosyl derivatives). Every query follows "product FROM lactose/whey" pattern. Covers different application areas: chemicals, emulsifiers, nutraceuticals, hydrogels.

### Example 21: Additive Manufacturing for Nutritional Supplements

- 3d printing for nutritional supplements
- 3d printing capsules medicine
- micro-dosing capsule 3d printing
- 3d printing food vitamins supplements
- 3d printing personalized vitamins
- 3d printing layered compartmentalized delivery tablet

**Pattern notes:** Compact set focused on a narrow topic. Uses "3d printing" as the consistent technology anchor. Varies the application (supplements, capsules, vitamins, tablets). Includes functional features (micro-dosing, layered, compartmentalized, personalized).

### Example 22: In-Line Viscosity Monitoring for Real-Time Molecular Weight Assessment

- molecular weight monitoring in polymer extrusion
- real time viscosity sensor polymer
- inline rheometry extrusion polymer sensor
- viscosity sensor polymer nozzle extrusion real time
- inline infrared spectroscopy for polymer melt degradation
- dielectric spectroscopy polymer molecular weight in extrusion

**Pattern notes:** Compact but covers key sensing modalities (rheometry, IR spectroscopy, dielectric spectroscopy, viscosity sensors). Anchors on "extrusion" and "polymer" context. Combines measurement technique + what's being measured + industrial process.

### Example 23: Shortwave Infrared Photodetector Materials and Growth Technologies

- black phosphorus compatible cmos swir
- epitaxial lift-off iii-v materials swir sensors cmos
- plasmonic nanostructures light-matter interaction swir photodetector sensitivity efficiency
- indium arsenide antimonide swir photodetector
- iii-v semiconductor materials heteroepitaxial growth on silicon cmos-compatible
- swir photodetector materials cmos detection wavelength 900 to 1700 nm
- direct wafer bonding integrating iii-v materials on silicon swir
- hydride vapor phase epitaxy nassb ingaas si substrates
- 2d materials nanophotonics for monolithic integration
- graphene compatible cmos swir
- transition metal dichalcogenides compatible cmos swir
- metal-organic chemical vapor deposition inassb ingaas si substrate

**Pattern notes:** Names specific materials (black phosphorus, InAsSb, InGaAs, graphene, TMDs). Names specific fabrication methods (epitaxial lift-off, HVPE, MOCVD, direct wafer bonding). Includes performance parameters (wavelength range). "CMOS compatible" anchors the integration requirement throughout.
