"""
Starter reference data for a brand-new organization. Used by both
seed.py (the bundled demo org) and the self-service "Register Your
Organization" signup flow, so a totally new, unrelated hospital group
isn't starting from a completely empty formulary/lab menu/radiology
menu/insurance list/ward setup on day one. Every hospital group can
freely edit or delete any of this afterward from Admin -> Catalogs —
it's a starting point, not a shared catalog.

DRUG SOURCE NOTE: The formulary below draws its INNs, dose-forms and
category groupings directly from the official Kenya Essential Medicines
List (KEML) 2023, Ministry of Health — the anaesthetics, pain/palliative,
antidote, anticonvulsant, anti-infective (antibacterial/antifungal/
antiviral/antimalarial/TB/HIV), antimigraine, blood, and cardiovascular
sections are sourced from the gazetted document. Sections the fetch
couldn't reach in full (dermatological, GI, endocrine, immunological,
ophthalmological, reproductive health, mental health, respiratory, ENT,
vitamins, electrolytes) are supplemented from standard WHO Model
List / Kenya EML staple medicines. If you have the full KEML as a
spreadsheet or PDF, send it over and this can be re-imported with the
same fidelity as the ICD-10 catalog. Strengths/forms are simplified to
one representative presentation per drug rather than every KEML
line-entry (which lists multiple strengths/forms per medicine).
"""
from app.extensions import db
from app.models import Drug, RadiologyTest, LabTest, InsuranceScheme, Ward

# (name, generic_name, form, strength, category)
STARTER_DRUGS = [
    # --- Anaesthetics & pre/intra-operative ---
    ("Ketamine", "Ketamine", "injection", "50mg/mL", "anaesthetic"),
    ("Propofol", "Propofol", "injection", "10mg/mL", "anaesthetic"),
    ("Thiopental Sodium", "Thiopental sodium", "injection", "500mg vial", "anaesthetic"),
    ("Bupivacaine", "Bupivacaine", "injection", "0.5%", "anaesthetic"),
    ("Lignocaine", "Lignocaine (Lidocaine)", "injection", "2%", "anaesthetic"),
    ("Lignocaine + Adrenaline", "Lignocaine + Epinephrine", "injection", "2% + 1:200,000", "anaesthetic"),
    ("Halothane", "Halothane", "inhalation", "250mL", "anaesthetic"),
    ("Isoflurane", "Isoflurane", "inhalation", "250mL", "anaesthetic"),
    ("Suxamethonium", "Suxamethonium chloride", "injection", "50mg/mL", "anaesthetic"),
    ("Atracurium", "Atracurium besilate", "injection", "10mg/mL", "anaesthetic"),
    ("Atropine", "Atropine sulphate", "injection", "1mg/mL", "anticholinergic"),
    ("Neostigmine", "Neostigmine metasulphate", "injection", "2.5mg/mL", "anticholinesterase"),
    ("Oxygen", "Oxygen", "medical gas", "-", "medical gas"),

    # --- Pain & palliative care ---
    ("Aspirin", "Acetylsalicylic acid", "tablet", "300mg", "analgesic"),
    ("Paracetamol", "Paracetamol", "tablet", "500mg", "analgesic"),
    ("Paracetamol Syrup", "Paracetamol", "oral liquid", "120mg/5mL", "analgesic"),
    ("Ibuprofen", "Ibuprofen", "tablet", "200mg", "analgesic"),
    ("Diclofenac", "Diclofenac sodium", "injection", "75mg/3mL", "analgesic"),
    ("Ketorolac", "Ketorolac", "injection", "30mg/mL", "analgesic"),
    ("Tramadol", "Tramadol", "capsule", "50mg", "analgesic"),
    ("Morphine", "Morphine", "injection", "10mg/mL", "opioid analgesic"),
    ("Morphine Oral", "Morphine", "oral liquid", "10mg/mL", "opioid analgesic"),
    ("Codeine", "Dihydrocodeine phosphate", "tablet", "30mg", "opioid analgesic"),
    ("Amitriptyline", "Amitriptyline", "tablet", "25mg", "adjunct analgesic"),
    ("Gabapentin", "Gabapentin", "tablet", "300mg", "adjunct analgesic"),
    ("Pregabalin", "Pregabalin", "capsule", "75mg", "adjunct analgesic"),
    ("Dexamethasone", "Dexamethasone", "tablet", "4mg", "corticosteroid"),
    ("Dexamethasone Injection", "Dexamethasone", "injection", "4mg/mL", "corticosteroid"),
    ("Hyoscine Butylbromide", "Hyoscine butylbromide", "injection", "20mg/mL", "antispasmodic"),
    ("Bisacodyl", "Bisacodyl", "tablet", "5mg", "laxative"),
    ("Senna", "Senna", "tablet", "7.5mg", "laxative"),
    ("Lactulose", "Lactulose", "oral liquid", "3.1-3.7g/5mL", "laxative"),
    ("Loperamide", "Loperamide", "capsule", "2mg", "antidiarrheal"),
    ("Metoclopramide", "Metoclopramide", "tablet", "10mg", "antiemetic"),
    ("Ondansetron", "Ondansetron", "tablet", "4mg", "antiemetic"),
    ("Ondansetron Injection", "Ondansetron", "injection", "2mg/mL", "antiemetic"),
    ("Haloperidol", "Haloperidol", "tablet", "5mg", "antipsychotic"),
    ("Diazepam", "Diazepam", "tablet", "5mg", "benzodiazepine"),
    ("Diazepam Injection", "Diazepam", "injection", "5mg/mL", "benzodiazepine"),

    # --- Antiallergics / anaphylaxis ---
    ("Cetirizine", "Cetirizine", "tablet", "10mg", "antihistamine"),
    ("Chlorpheniramine", "Chlorpheniramine maleate", "injection", "10mg/mL", "antihistamine"),
    ("Loratadine", "Loratadine", "tablet", "10mg", "antihistamine"),
    ("Adrenaline", "Epinephrine (adrenaline)", "injection", "1mg/mL", "emergency/anaphylaxis"),
    ("Hydrocortisone Injection", "Hydrocortisone sodium succinate", "injection", "100mg vial", "corticosteroid"),
    ("Diphenhydramine", "Diphenhydramine", "injection", "50mg/mL", "antihistamine"),

    # --- Antidotes / poisoning ---
    ("Activated Charcoal", "Activated charcoal", "oral powder", "50g", "antidote"),
    ("Calcium Gluconate", "Calcium gluconate", "injection", "100mg/mL", "antidote"),
    ("Naloxone", "Naloxone", "injection", "400mcg/mL", "antidote"),
    ("Vitamin K1", "Phytomenadione", "injection", "10mg/mL", "antidote"),
    ("Sodium Bicarbonate", "Sodium hydrogen carbonate", "injection", "8.4%", "antidote"),
    ("Flumazenil", "Flumazenil", "injection", "100mcg/mL", "antidote"),
    ("N-Acetylcysteine", "Acetylcysteine", "injection", "200mg/mL", "antidote"),

    # --- Anticonvulsants ---
    ("Carbamazepine", "Carbamazepine", "tablet", "200mg", "anticonvulsant"),
    ("Sodium Valproate", "Valproic acid", "tablet", "500mg", "anticonvulsant"),
    ("Phenytoin", "Phenytoin sodium", "tablet", "100mg", "anticonvulsant"),
    ("Phenytoin Injection", "Phenytoin sodium", "injection", "50mg/mL", "anticonvulsant"),
    ("Phenobarbital", "Phenobarbital sodium", "tablet", "30mg", "anticonvulsant"),
    ("Levetiracetam", "Levetiracetam", "tablet", "500mg", "anticonvulsant"),
    ("Lamotrigine", "Lamotrigine", "tablet", "100mg", "anticonvulsant"),
    ("Magnesium Sulphate", "Magnesium sulphate", "injection", "500mg/mL (50%)", "anticonvulsant/obstetric"),
    ("Lorazepam", "Lorazepam", "injection", "4mg/mL", "anticonvulsant"),

    # --- Antibacterials (Access) ---
    ("Amoxicillin", "Amoxicillin", "capsule", "500mg", "antibiotic"),
    ("Amoxicillin Suspension", "Amoxicillin", "oral liquid", "250mg/5mL", "antibiotic"),
    ("Amoxicillin-Clavulanate", "Amoxicillin + Clavulanic acid", "tablet", "1g (875+125mg)", "antibiotic"),
    ("Ampicillin", "Ampicillin", "injection", "500mg vial", "antibiotic"),
    ("Benzylpenicillin", "Benzylpenicillin (Crystalline Penicillin)", "injection", "600mg (1MU)", "antibiotic"),
    ("Benzathine Benzylpenicillin", "Benzathine benzylpenicillin", "injection", "1.2MU vial", "antibiotic"),
    ("Flucloxacillin", "Flucloxacillin", "capsule", "500mg", "antibiotic"),
    ("Cefalexin", "Cefalexin", "capsule", "250mg", "antibiotic"),
    ("Cefazolin", "Cefazolin", "injection", "1g vial", "antibiotic"),
    ("Doxycycline", "Doxycycline", "capsule", "100mg", "antibiotic"),
    ("Gentamicin", "Gentamicin", "injection", "40mg/mL", "antibiotic"),
    ("Amikacin", "Amikacin", "injection", "250mg/mL", "antibiotic"),
    ("Metronidazole", "Metronidazole", "tablet", "400mg", "antibiotic"),
    ("Metronidazole Injection", "Metronidazole", "injection", "5mg/mL", "antibiotic"),
    ("Nitrofurantoin", "Nitrofurantoin", "tablet", "100mg", "antibiotic"),
    ("Penicillin V", "Phenoxymethylpenicillin", "tablet", "250mg", "antibiotic"),

    # --- Antibacterials (Watch) ---
    ("Azithromycin", "Azithromycin", "tablet", "500mg", "antibiotic"),
    ("Cefixime", "Cefixime", "tablet", "400mg", "antibiotic"),
    ("Cefotaxime", "Cefotaxime", "injection", "1g vial", "antibiotic"),
    ("Ceftazidime", "Ceftazidime", "injection", "1g vial", "antibiotic"),
    ("Ceftriaxone", "Ceftriaxone", "injection", "1g vial", "antibiotic"),
    ("Cefuroxime", "Cefuroxime", "injection", "750mg vial", "antibiotic"),
    ("Ciprofloxacin", "Ciprofloxacin", "tablet", "500mg", "antibiotic"),
    ("Clarithromycin", "Clarithromycin", "tablet", "500mg", "antibiotic"),
    ("Clindamycin", "Clindamycin", "capsule", "150mg", "antibiotic"),
    ("Cotrimoxazole", "Sulfamethoxazole + Trimethoprim", "tablet", "800+160mg", "antibiotic"),
    ("Erythromycin", "Erythromycin", "tablet", "500mg", "antibiotic"),
    ("Piperacillin-Tazobactam", "Piperacillin + Tazobactam", "injection", "4g+500mg vial", "antibiotic"),

    # --- Antibacterials (Reserve) ---
    ("Meropenem", "Meropenem", "injection", "500mg vial", "antibiotic"),
    ("Vancomycin", "Vancomycin", "injection", "500mg vial", "antibiotic"),
    ("Linezolid", "Linezolid", "tablet", "600mg", "antibiotic"),

    # --- Antituberculosis ---
    ("Ethambutol", "Ethambutol", "tablet", "400mg", "antituberculosis"),
    ("Isoniazid", "Isoniazid", "tablet", "300mg", "antituberculosis"),
    ("Pyrazinamide", "Pyrazinamide", "tablet", "500mg", "antituberculosis"),
    ("Rifampicin", "Rifampicin", "capsule", "300mg", "antituberculosis"),
    ("RHZE (4FDC)", "Rifampicin+Isoniazid+Pyrazinamide+Ethambutol", "tablet", "150+75+400+275mg", "antituberculosis"),
    ("RH (2FDC)", "Rifampicin + Isoniazid", "tablet", "150+75mg", "antituberculosis"),

    # --- Antifungals ---
    ("Fluconazole", "Fluconazole", "capsule", "150mg", "antifungal"),
    ("Nystatin", "Nystatin", "oral liquid", "100,000 IU/mL", "antifungal"),
    ("Clotrimazole", "Clotrimazole", "vaginal tablet", "500mg", "antifungal"),
    ("Griseofulvin", "Griseofulvin", "tablet", "500mg", "antifungal"),
    ("Amphotericin B", "Amphotericin B", "injection", "50mg vial", "antifungal"),
    ("Terbinafine", "Terbinafine", "tablet", "250mg", "antifungal"),

    # --- Antivirals / ARVs ---
    ("Acyclovir", "Acyclovir", "tablet", "400mg", "antiviral"),
    ("Tenofovir-Lamivudine-Dolutegravir", "TDF+3TC+DTG", "tablet", "300+300+50mg", "antiretroviral"),
    ("Abacavir-Lamivudine", "Abacavir + Lamivudine", "tablet", "600+300mg", "antiretroviral"),
    ("Zidovudine-Lamivudine", "Zidovudine + Lamivudine", "tablet", "300+150mg", "antiretroviral"),
    ("Nevirapine", "Nevirapine", "oral liquid", "10mg/mL", "antiretroviral"),
    ("Dolutegravir", "Dolutegravir", "tablet", "50mg", "antiretroviral"),
    ("Lopinavir-Ritonavir", "Lopinavir + Ritonavir", "tablet", "200+50mg", "antiretroviral"),

    # --- Antimalarials ---
    ("Artemether-Lumefantrine", "Artemether + Lumefantrine", "tablet", "20+120mg", "antimalarial"),
    ("Artesunate Injection", "Artesunate", "injection", "60mg vial", "antimalarial"),
    ("Dihydroartemisinin-Piperaquine", "DHA-Piperaquine", "tablet", "40+320mg", "antimalarial"),
    ("Quinine", "Quinine", "tablet", "300mg", "antimalarial"),
    ("Quinine Injection", "Quinine", "injection", "300mg/mL", "antimalarial"),
    ("Sulfadoxine-Pyrimethamine", "Sulfadoxine + Pyrimethamine", "tablet", "500+25mg", "antimalarial"),
    ("Malaria RDT Treatment - Artemether Oily", "Artemether", "injection", "80mg/mL", "antimalarial"),

    # --- Anthelminthics / antiprotozoals ---
    ("Albendazole", "Albendazole", "tablet", "400mg", "anthelminthic"),
    ("Mebendazole", "Mebendazole", "tablet", "500mg", "anthelminthic"),
    ("Praziquantel", "Praziquantel", "tablet", "600mg", "anthelminthic"),
    ("Ivermectin", "Ivermectin", "tablet", "3mg", "anthelminthic"),
    ("Diloxanide", "Diloxanide furoate", "tablet", "500mg", "antiamoebic"),

    # --- Antimigraine ---
    ("Sumatriptan", "Sumatriptan", "tablet", "50mg", "antimigraine"),
    ("Propranolol", "Propranolol", "tablet", "40mg", "antimigraine prophylaxis"),

    # --- Antiparkinsonism / dementia ---
    ("Benzhexol", "Benzhexol (Trihexyphenidyl)", "tablet", "5mg", "antiparkinsonism"),
    ("Levodopa-Carbidopa", "Levodopa + Carbidopa", "tablet", "250+25mg", "antiparkinsonism"),
    ("Donepezil", "Donepezil", "tablet", "5mg", "anti-dementia"),

    # --- Blood / haematinics / anticoagulants ---
    ("Ferrous Sulphate", "Ferrous salt", "tablet", "60-65mg elemental iron", "haematinic"),
    ("Ferrous Sulphate + Folic Acid", "Ferrous salt + Folic acid", "tablet", "60mg + 400mcg", "haematinic"),
    ("Folic Acid", "Folic acid", "tablet", "5mg", "haematinic"),
    ("Vitamin B12", "Hydroxocobalamin", "injection", "1mg/mL", "haematinic"),
    ("Iron Sucrose", "Iron sucrose", "injection", "100mg", "haematinic"),
    ("Tranexamic Acid", "Tranexamic acid", "tablet", "500mg", "haemostatic"),
    ("Tranexamic Acid Injection", "Tranexamic acid", "injection", "100mg/mL", "haemostatic"),
    ("Heparin", "Heparin sodium", "injection", "5,000 IU/mL", "anticoagulant"),
    ("Enoxaparin", "Enoxaparin", "injection", "40mg/0.4mL", "anticoagulant"),
    ("Warfarin", "Warfarin", "tablet", "5mg", "anticoagulant"),
    ("Rivaroxaban", "Rivaroxaban", "tablet", "20mg", "anticoagulant"),
    ("Hydroxyurea", "Hydroxycarbamide", "capsule", "500mg", "haemoglobinopathy"),

    # --- Cardiovascular ---
    ("Bisoprolol", "Bisoprolol", "tablet", "5mg", "cardiovascular"),
    ("Carvedilol", "Carvedilol", "tablet", "6.25mg", "cardiovascular"),
    ("Glyceryl Trinitrate", "Glyceryl trinitrate", "sublingual tablet", "500mcg", "cardiovascular"),
    ("Isosorbide Dinitrate", "Isosorbide dinitrate", "tablet", "20mg", "cardiovascular"),
    ("Amiodarone", "Amiodarone", "tablet", "200mg", "antiarrhythmic"),
    ("Digoxin", "Digoxin", "tablet", "125mcg", "cardiovascular"),
    ("Verapamil", "Verapamil", "tablet", "40mg", "cardiovascular"),
    ("Enalapril", "Enalapril", "tablet", "10mg", "antihypertensive"),
    ("Losartan", "Losartan", "tablet", "50mg", "antihypertensive"),
    ("Telmisartan", "Telmisartan", "tablet", "40mg", "antihypertensive"),
    ("Labetalol", "Labetalol", "tablet", "200mg", "antihypertensive"),
    ("Labetalol Injection", "Labetalol", "injection", "5mg/mL", "antihypertensive"),
    ("Metoprolol", "Metoprolol", "tablet", "50mg", "antihypertensive"),
    ("Amlodipine", "Amlodipine", "tablet", "5mg", "antihypertensive"),
    ("Nifedipine", "Nifedipine", "tablet (slow release)", "20mg", "antihypertensive"),
    ("Hydrochlorothiazide", "Hydrochlorothiazide", "tablet", "25mg", "diuretic"),
    ("Indapamide", "Indapamide", "tablet", "1.5mg", "diuretic"),
    ("Spironolactone", "Spironolactone", "tablet", "25mg", "diuretic"),
    ("Furosemide", "Furosemide", "tablet", "40mg", "diuretic"),
    ("Furosemide Injection", "Furosemide", "injection", "10mg/mL", "diuretic"),
    ("Methyldopa", "Methyldopa", "tablet", "250mg", "antihypertensive (pregnancy)"),
    ("Hydralazine", "Hydralazine", "tablet", "25mg", "antihypertensive"),
    ("Hydralazine Injection", "Hydralazine", "injection", "20mg vial", "antihypertensive"),
    ("Amlodipine-HCTZ", "Amlodipine + Hydrochlorothiazide", "tablet", "5+12.5mg", "antihypertensive"),
    ("Dobutamine", "Dobutamine", "injection", "12.5mg/mL", "inotrope"),
    ("Dopamine", "Dopamine", "injection", "40mg/mL", "inotrope"),

    # --- Dermatological ---
    ("Hydrocortisone Cream", "Hydrocortisone", "topical cream", "1%", "dermatological"),
    ("Betamethasone Cream", "Betamethasone valerate", "topical cream", "0.1%", "dermatological"),
    ("Clotrimazole Cream", "Clotrimazole", "topical cream", "1%", "dermatological"),
    ("Benzyl Benzoate", "Benzyl benzoate", "lotion", "25%", "dermatological"),
    ("Permethrin", "Permethrin", "cream", "5%", "dermatological"),
    ("Silver Sulfadiazine", "Silver sulfadiazine", "cream", "1%", "dermatological (burns)"),
    ("Calamine Lotion", "Calamine", "lotion", "-", "dermatological"),
    ("Zinc Oxide Ointment", "Zinc oxide", "ointment", "-", "dermatological"),

    # --- Gastrointestinal ---
    ("Omeprazole", "Omeprazole", "capsule", "20mg", "gastrointestinal"),
    ("Ranitidine", "Ranitidine", "tablet", "150mg", "gastrointestinal"),
    ("Magnesium Trisilicate", "Magnesium trisilicate", "tablet (chewable)", "500mg", "antacid"),
    ("Domperidone", "Domperidone", "tablet", "10mg", "gastrointestinal"),
    ("Simethicone", "Simethicone", "tablet (chewable)", "125mg", "gastrointestinal"),
    ("Oral Rehydration Salts", "Oral Rehydration Salts (ORS)", "sachet", "20.5g", "rehydration"),
    ("Zinc Sulphate", "Zinc sulphate", "tablet (dispersible)", "20mg", "rehydration adjunct"),

    # --- Endocrine / diabetes ---
    ("Insulin Soluble", "Insulin (soluble/regular)", "injection", "100 IU/mL", "antidiabetic"),
    ("Insulin NPH", "Insulin (isophane/NPH)", "injection", "100 IU/mL", "antidiabetic"),
    ("Metformin", "Metformin", "tablet", "500mg", "antidiabetic"),
    ("Glibenclamide", "Glibenclamide", "tablet", "5mg", "antidiabetic"),
    ("Gliclazide", "Gliclazide", "tablet", "80mg", "antidiabetic"),
    ("Levothyroxine", "Levothyroxine", "tablet", "100mcg", "endocrine"),
    ("Carbimazole", "Carbimazole", "tablet", "5mg", "endocrine"),
    ("Prednisolone", "Prednisolone", "tablet", "5mg", "corticosteroid"),

    # --- Immunologicals (vaccines) ---
    ("BCG Vaccine", "BCG vaccine", "injection", "-", "vaccine"),
    ("OPV", "Oral Polio Vaccine", "oral drops", "-", "vaccine"),
    ("Pentavalent Vaccine", "DPT-HepB-Hib", "injection", "-", "vaccine"),
    ("Measles-Rubella Vaccine", "MR vaccine", "injection", "-", "vaccine"),
    ("Tetanus Toxoid", "Tetanus toxoid", "injection", "-", "vaccine"),
    ("Hepatitis B Vaccine", "Hepatitis B vaccine", "injection", "-", "vaccine"),
    ("Pneumococcal Vaccine", "PCV", "injection", "-", "vaccine"),
    ("Rotavirus Vaccine", "Rotavirus vaccine", "oral drops", "-", "vaccine"),
    ("Rabies Vaccine", "Rabies vaccine", "injection", "-", "vaccine"),
    ("Anti-Tetanus Immunoglobulin", "Anti-Tetanus Immunoglobulin", "injection", "1500 IU vial", "immunoglobulin"),
    ("Anti-Rabies Immunoglobulin", "Anti-Rabies Immunoglobulin", "injection", "200 IU/mL", "immunoglobulin"),

    # --- Ophthalmological ---
    ("Chloramphenicol Eye Drops", "Chloramphenicol", "eye drops", "0.5%", "ophthalmological"),
    ("Tetracycline Eye Ointment", "Tetracycline", "eye ointment", "1%", "ophthalmological"),
    ("Gentamicin Eye Drops", "Gentamicin", "eye drops", "0.3%", "ophthalmological"),
    ("Timolol Eye Drops", "Timolol", "eye drops", "0.5%", "ophthalmological"),
    ("Atropine Eye Drops", "Atropine", "eye drops", "1%", "ophthalmological"),

    # --- Reproductive health / perinatal ---
    ("Oxytocin", "Oxytocin", "injection", "10 IU/mL", "obstetric"),
    ("Ergometrine", "Ergometrine", "injection", "500mcg/mL", "obstetric"),
    ("Misoprostol", "Misoprostol", "tablet", "200mcg", "obstetric"),
    ("Dexamethasone Antenatal", "Dexamethasone", "injection", "6mg/mL", "obstetric (fetal lung maturity)"),
    ("Combined Oral Contraceptive", "Ethinylestradiol + Levonorgestrel", "tablet", "30mcg+150mcg", "contraceptive"),
    ("Depot Medroxyprogesterone", "Medroxyprogesterone acetate", "injection", "150mg/mL", "contraceptive"),
    ("Levonorgestrel (Emergency)", "Levonorgestrel", "tablet", "1.5mg", "contraceptive"),
    ("Copper IUD", "Copper intrauterine device", "device", "-", "contraceptive"),

    # --- Mental health ---
    ("Chlorpromazine", "Chlorpromazine", "tablet", "100mg", "antipsychotic"),
    ("Risperidone", "Risperidone", "tablet", "2mg", "antipsychotic"),
    ("Olanzapine", "Olanzapine", "tablet", "10mg", "antipsychotic"),
    ("Fluoxetine", "Fluoxetine", "capsule", "20mg", "antidepressant"),
    ("Lithium Carbonate", "Lithium carbonate", "tablet", "400mg", "mood stabilizer"),

    # --- Respiratory ---
    ("Salbutamol Inhaler", "Salbutamol", "inhaler", "100mcg/dose", "respiratory"),
    ("Salbutamol Nebules", "Salbutamol", "nebule", "2.5mg/2.5mL", "respiratory"),
    ("Beclometasone Inhaler", "Beclometasone", "inhaler", "100mcg/dose", "respiratory"),
    ("Ipratropium Bromide", "Ipratropium bromide", "nebule", "250mcg/mL", "respiratory"),
    ("Aminophylline", "Aminophylline", "injection", "25mg/mL", "respiratory"),

    # --- ENT ---
    ("Xylometazoline Nasal Drops", "Xylometazoline", "nasal drops", "0.1%", "ENT"),
    ("Chloramphenicol Ear Drops", "Chloramphenicol", "ear drops", "5%", "ENT"),

    # --- Rheumatology ---
    ("Sulfasalazine", "Sulfasalazine", "tablet", "500mg", "rheumatology"),
    ("Allopurinol", "Allopurinol", "tablet", "300mg", "rheumatology"),
    ("Colchicine", "Colchicine", "tablet", "500mcg", "rheumatology"),
    ("Methotrexate", "Methotrexate", "tablet", "2.5mg", "rheumatology/oncology"),

    # --- Vitamins / minerals ---
    ("Vitamin A", "Retinol", "capsule", "200,000 IU", "vitamin"),
    ("Vitamin B Complex", "Vitamin B complex", "tablet", "-", "vitamin"),
    ("Vitamin C", "Ascorbic acid", "tablet", "100mg", "vitamin"),
    ("Vitamin D3", "Cholecalciferol", "tablet", "1000 IU", "vitamin"),
    ("Calcium Carbonate", "Calcium carbonate", "tablet", "500mg", "mineral"),
    ("Multivitamin Syrup", "Multivitamin", "oral liquid", "-", "vitamin"),

    # --- IV fluids / electrolytes ---
    ("Normal Saline", "Sodium chloride 0.9%", "IV infusion", "500mL/1L", "IV fluid"),
    ("Dextrose 5%", "Dextrose (Glucose) 5%", "IV infusion", "500mL/1L", "IV fluid"),
    ("Dextrose 50%", "Dextrose (Glucose) 50%", "injection", "50mL", "IV fluid"),
    ("Ringer's Lactate", "Compound Sodium Lactate", "IV infusion", "500mL/1L", "IV fluid"),
    ("Potassium Chloride", "Potassium chloride", "injection (concentrate)", "20mmol/10mL", "electrolyte"),
    ("Calcium Gluconate 10%", "Calcium gluconate", "injection", "10%", "electrolyte"),

    # --- Blood products ---
    ("Whole Blood", "Whole blood", "transfusion unit", "450mL", "blood product"),
    ("Packed Red Cells", "Red blood cells", "transfusion unit", "-", "blood product"),
    ("Fresh Frozen Plasma", "Plasma, fresh-frozen", "transfusion unit", "-", "blood product"),
    ("Platelets", "Platelets", "transfusion unit", "-", "blood product"),
    ("Human Albumin", "Human albumin infusion", "injection", "20%", "blood product"),
]

# (code, name, modality, price)
STARTER_RADIOLOGY = [
    ("XR-CHEST", "Chest X-Ray", "X-Ray", 1000),
    ("XR-ABD", "Abdominal X-Ray", "X-Ray", 1200),
    ("XR-SKULL", "Skull X-Ray", "X-Ray", 1200),
    ("XR-CSPINE", "Cervical Spine X-Ray", "X-Ray", 1300),
    ("XR-LSPINE", "Lumbosacral Spine X-Ray", "X-Ray", 1300),
    ("XR-PELVIS", "Pelvis X-Ray", "X-Ray", 1200),
    ("XR-LIMB", "Limb X-Ray (Upper/Lower)", "X-Ray", 1000),
    ("XR-HAND", "Hand X-Ray", "X-Ray", 900),
    ("XR-FOOT", "Foot X-Ray", "X-Ray", 900),
    ("XR-KUB", "KUB X-Ray (Kidney-Ureter-Bladder)", "X-Ray", 1300),
    ("XR-SINUS", "Paranasal Sinus X-Ray", "X-Ray", 1100),
    ("USS-ABD", "Abdominal Ultrasound", "Ultrasound", 2000),
    ("USS-PELVIC", "Pelvic Ultrasound", "Ultrasound", 2000),
    ("USS-OB-DATE", "Obstetric Ultrasound (Dating)", "Ultrasound", 2000),
    ("USS-OB-ANOM", "Obstetric Ultrasound (Anomaly Scan)", "Ultrasound", 3000),
    ("USS-OB-GROWTH", "Obstetric Ultrasound (Growth Scan)", "Ultrasound", 2500),
    ("USS-RENAL", "Renal Ultrasound", "Ultrasound", 2000),
    ("USS-THYROID", "Thyroid Ultrasound", "Ultrasound", 2200),
    ("USS-BREAST", "Breast Ultrasound", "Ultrasound", 2200),
    ("USS-SCROTAL", "Scrotal Ultrasound", "Ultrasound", 2200),
    ("USS-DOPPLER", "Doppler Ultrasound (Venous/Arterial)", "Ultrasound", 3500),
    ("ECHO", "Echocardiogram", "Ultrasound", 4500),
    ("USS-SOFT", "Soft Tissue Ultrasound", "Ultrasound", 2000),
    ("USS-FAST", "FAST Scan (Trauma)", "Ultrasound", 2500),
    ("CT-HEAD", "CT Scan - Head", "CT", 8000),
    ("CT-CHEST", "CT Scan - Chest", "CT", 10000),
    ("CT-ABD-PELVIS", "CT Scan - Abdomen & Pelvis", "CT", 12000),
    ("CT-SPINE", "CT Scan - Spine", "CT", 10000),
    ("CT-ANGIO", "CT Angiography", "CT", 15000),
    ("CT-KUB", "CT KUB (Stone Protocol)", "CT", 9000),
    ("MRI-BRAIN", "MRI - Brain", "MRI", 15000),
    ("MRI-SPINE", "MRI - Spine", "MRI", 16000),
    ("MRI-KNEE", "MRI - Knee", "MRI", 14000),
    ("MRI-ABD", "MRI - Abdomen", "MRI", 17000),
    ("MRI-PELVIS", "MRI - Pelvis", "MRI", 16000),
    ("MAMMO", "Mammography", "Mammography", 3500),
    ("FLUORO-BARIUM-SWALLOW", "Barium Swallow", "Fluoroscopy", 4000),
    ("FLUORO-BARIUM-MEAL", "Barium Meal", "Fluoroscopy", 4500),
    ("FLUORO-BARIUM-ENEMA", "Barium Enema", "Fluoroscopy", 5000),
    ("IVU", "Intravenous Urogram (IVU)", "Fluoroscopy", 5500),
    ("HSG", "Hysterosalpingogram (HSG)", "Fluoroscopy", 5000),
    ("DEXA", "Bone Density Scan (DEXA)", "Other", 4000),
]

# (code, name, category, price)
STARTER_LAB_TESTS = [
    # Hematology
    ("CBC", "Complete Blood Count", "Hematology", 500),
    ("HB", "Haemoglobin", "Hematology", 200),
    ("ESR", "Erythrocyte Sedimentation Rate", "Hematology", 300),
    ("RETIC", "Reticulocyte Count", "Hematology", 400),
    ("PBF", "Peripheral Blood Film", "Hematology", 400),
    ("SICKLE", "Sickling Test", "Hematology", 300),
    ("HBELECT", "Haemoglobin Electrophoresis", "Hematology", 1500),
    ("PLT", "Platelet Count", "Hematology", 300),
    ("BGRP", "Blood Group & Rhesus Factor", "Hematology", 300),
    ("COOMBS", "Coombs Test (Direct/Indirect)", "Hematology", 800),
    # Coagulation
    ("PT-INR", "Prothrombin Time / INR", "Coagulation", 600),
    ("APTT", "Activated Partial Thromboplastin Time", "Coagulation", 600),
    ("DDIMER", "D-Dimer", "Coagulation", 1500),
    ("BT", "Bleeding Time", "Coagulation", 200),
    # Chemistry
    ("RBS", "Random Blood Sugar", "Chemistry", 150),
    ("FBS", "Fasting Blood Sugar", "Chemistry", 200),
    ("OGTT", "Oral Glucose Tolerance Test", "Chemistry", 800),
    ("HBA1C", "HbA1c (Glycated Haemoglobin)", "Chemistry", 1200),
    ("UECR", "Urea, Electrolytes & Creatinine", "Chemistry", 1500),
    ("EGFR", "Estimated GFR", "Chemistry", 300),
    ("LFT", "Liver Function Tests", "Chemistry", 1500),
    ("TP-ALB", "Total Protein & Albumin", "Chemistry", 600),
    ("LIPID", "Lipid Profile", "Chemistry", 1200),
    ("URIC", "Uric Acid", "Chemistry", 400),
    ("CALCIUM", "Serum Calcium", "Chemistry", 400),
    ("PHOS", "Serum Phosphate", "Chemistry", 400),
    ("MAG", "Serum Magnesium", "Chemistry", 500),
    ("AMYLASE", "Serum Amylase", "Chemistry", 700),
    ("LIPASE", "Serum Lipase", "Chemistry", 800),
    ("CK", "Creatine Kinase (CK)", "Chemistry", 700),
    ("CKMB", "CK-MB", "Chemistry", 900),
    ("TROPONIN", "Troponin I/T", "Chemistry", 1800),
    ("LDH", "Lactate Dehydrogenase (LDH)", "Chemistry", 600),
    ("CRP", "C-Reactive Protein (CRP)", "Chemistry", 800),
    ("PROCAL", "Procalcitonin", "Chemistry", 2500),
    ("ABG", "Arterial Blood Gas (ABG)", "Chemistry", 1500),
    # Microbiology
    ("BCULT", "Blood Culture & Sensitivity", "Microbiology", 1500),
    ("UCULT", "Urine Culture & Sensitivity", "Microbiology", 1000),
    ("SCULT", "Stool Culture & Sensitivity", "Microbiology", 1000),
    ("SPCULT", "Sputum Culture & Sensitivity", "Microbiology", 1200),
    ("WCULT", "Wound Swab Culture & Sensitivity", "Microbiology", 1000),
    ("THCULT", "Throat Swab Culture & Sensitivity", "Microbiology", 900),
    ("CSFCULT", "CSF Culture & Sensitivity", "Microbiology", 1500),
    ("GRAM", "Gram Stain", "Microbiology", 400),
    ("ZN", "AFB / ZN Stain (Sputum)", "Microbiology", 400),
    ("GENEXPERT", "GeneXpert MTB/RIF", "Microbiology", 2500),
    ("KOH", "Fungal Prep (KOH)", "Microbiology", 400),
    # Parasitology
    ("MRDT", "Malaria Rapid Diagnostic Test", "Parasitology", 200),
    ("BS-MALARIA", "Malaria Blood Slide", "Parasitology", 150),
    ("STOOLOP", "Stool Microscopy (Ova & Parasites)", "Parasitology", 300),
    ("STOOLOB", "Stool for Occult Blood", "Parasitology", 300),
    ("URINESCHISTO", "Urine Microscopy for Schistosomiasis", "Parasitology", 300),
    # Serology / Immunology
    ("HIV-RDT", "HIV Rapid Test", "Serology", 200),
    ("HIV-ELISA", "HIV ELISA", "Serology", 800),
    ("HIVVL", "HIV Viral Load", "Serology", 3500),
    ("CD4", "CD4 Count", "Serology", 2000),
    ("HBSAG", "Hepatitis B Surface Antigen (HBsAg)", "Serology", 500),
    ("HCVAB", "Hepatitis C Antibody", "Serology", 600),
    ("VDRL", "VDRL / RPR (Syphilis)", "Serology", 400),
    ("TPHA", "TPHA (Syphilis Confirmatory)", "Serology", 600),
    ("WIDAL", "Widal Test (Typhoid)", "Serology", 400),
    ("RF", "Rheumatoid Factor", "Serology", 600),
    ("ANA", "Antinuclear Antibody (ANA)", "Serology", 1800),
    ("ASOT", "ASOT (Anti-Streptolysin O Titre)", "Serology", 600),
    ("HPYLORI", "H. Pylori Antigen/Antibody", "Serology", 800),
    ("DENGUE", "Dengue NS1/IgM", "Serology", 1200),
    ("BRUCELLA", "Brucella Agglutination Test", "Serology", 600),
    ("BHCG", "Beta-hCG (Pregnancy Test)", "Serology", 300),
    # Hormones / endocrine
    ("TSH", "Thyroid Stimulating Hormone (TSH)", "Endocrine", 1000),
    ("FT4", "Free T4", "Endocrine", 1000),
    ("FT3", "Free T3", "Endocrine", 1000),
    ("FSH", "Follicle Stimulating Hormone (FSH)", "Endocrine", 1200),
    ("LH", "Luteinizing Hormone (LH)", "Endocrine", 1200),
    ("PROLACTIN", "Prolactin", "Endocrine", 1200),
    ("TESTOSTERONE", "Testosterone", "Endocrine", 1500),
    ("CORTISOL", "Cortisol", "Endocrine", 1500),
    # Tumor markers
    ("PSA", "Prostate Specific Antigen (PSA)", "Tumor Marker", 1800),
    ("CA125", "CA-125", "Tumor Marker", 2000),
    ("CA199", "CA 19-9", "Tumor Marker", 2000),
    ("CEA", "Carcinoembryonic Antigen (CEA)", "Tumor Marker", 1800),
    ("AFP", "Alpha-Fetoprotein (AFP)", "Tumor Marker", 1800),
    # Urinalysis
    ("URINALYSIS", "Urinalysis (Dipstick)", "Urinalysis", 300),
    ("URINEMIC", "Urine Microscopy", "Urinalysis", 400),
    ("URINEPROT24", "24-Hour Urine Protein", "Urinalysis", 800),
    # Histopathology / cytology
    ("PAPSMEAR", "Pap Smear", "Histopathology", 1000),
    ("HISTOLOGY", "Biopsy Histology", "Histopathology", 3000),
    ("FNAC", "Fine Needle Aspiration Cytology (FNAC)", "Histopathology", 1800),
    # Blood bank
    ("CROSSMATCH", "Blood Grouping & Crossmatch", "Blood Bank", 800),
    ("ABSCREEN", "Antibody Screening", "Blood Bank", 1200),
]

STARTER_INSURANCE = [
    # (name, code, scheme_type)
    ("Social Health Authority", "SHA", "NHIF/SHA"),
    ("AAR Insurance", "AAR", "Private"),
    ("Jubilee Health", "JUBILEE", "Private"),
    ("Cash / Self-pay", "CASH", "Private"),
]

STARTER_WARDS = [
    # (name, ward_type, total_beds, daily_rate)
    ("General Ward A", "General", 20, 1500),
    ("Maternity Ward", "Maternity", 10, 2000),
    ("ICU", "ICU", 4, 8000),
    ("Pediatric Ward", "Pediatric", 12, 1800),
]


def seed_starter_catalog_for_org(organization, hospital):
    """Populates a fresh organization's formulary, lab menu, radiology
    menu, insurance list, and first hospital's wards. Does not commit —
    caller controls the transaction."""
    for name, generic, form, strength, category in STARTER_DRUGS:
        db.session.add(Drug(
            organization_id=organization.id, name=name, generic_name=generic,
            form=form, strength=strength, category=category,
        ))

    for code, name, modality, price in STARTER_RADIOLOGY:
        db.session.add(RadiologyTest(
            organization_id=organization.id, code=code, name=name, modality=modality, price=price,
        ))

    for code, name, category, price in STARTER_LAB_TESTS:
        db.session.add(LabTest(
            organization_id=organization.id, code=code, name=name, category=category, price=price,
        ))

    for name, code, scheme_type in STARTER_INSURANCE:
        db.session.add(InsuranceScheme(
            organization_id=organization.id, name=name, code=code, scheme_type=scheme_type,
        ))

    for name, ward_type, total_beds, daily_rate in STARTER_WARDS:
        db.session.add(Ward(
            hospital_id=hospital.id, name=name, ward_type=ward_type,
            total_beds=total_beds, daily_rate=daily_rate,
        ))

    hospital.set_setting("default_consultation_fee", 500)
