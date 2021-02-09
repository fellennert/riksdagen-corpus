"""
Implements the segmentation of the data into speeches and
ultimately into the Parla-Clarin XML format.
"""
import numpy as np
import pandas as pd
import re, hashlib, copy, os
import progressbar
from os import listdir
from os.path import isfile, join
from lxml import etree
from riksdagen_corpus.mp import detect_mp
from riksdagen_corpus.download import get_blocks, fetch_files
from riksdagen_corpus.utils import infer_metadata
from riksdagen_corpus.db import filter_db, year_iterator

# Classify paragraph
def classify_paragraph(paragraph, classifier, prior=np.log([0.8, 0.2])):
    """
    Classify paragraph into speeches / descriptions with provided classifier

    """
    words = paragraph.split()
    V = len(words)
    if V == 0:
        return prior

    x = np.zeros((V, classifier["dim"]))

    ft = classifier["ft"]
    for ix, word in enumerate(words):
        vec = ft.get_word_vector(word)
        x[ix] = vec

    pred = classifier["model"].predict(x, batch_size=V)
    return np.sum(pred, axis=0) + prior

def _is_metadata_block(txt0):
    txt1 = re.sub("[^a-zA-ZåäöÅÄÖ ]+", "", txt0)
    len0 = len(txt0)
    
    # Empty blocks should not be classified as metadata
    if len(txt0.strip()) == 0:
        return False
        
    # Metadata generally don't introduce other things
    if txt0.strip()[-1] == ":":
        return False

    # Or list MPs
    if "Anf." in txt0:
        return False
    
    len1 = len(txt1)
    len2 = len(txt0.strip())
    if len2 == 0:
        return False
    
    # Crude heuristic. Skip if
    # a) over 15% is non alphabetic characters
    # and b) length is under 150 characters
    
    # TODO: replace with ML algorithm
    return float(len1) / float(len0) < 0.85 and len0 < 150

def _detect_mp(matched_txt, names_ids):
    person = None
    for name, identifier in names_ids:
        if name in matched_txt:
            person = identifier
    
    if person == None:
        for name, identifier in names_ids:
            if name.upper() in matched_txt:
                person = identifier
    
    # Only match last name if full name is not found
    if person == None:
        for name, identifier in names_ids:
            last_name = " " + name.split()[-1]
            if last_name in matched_txt:
                person = identifier
            elif last_name.upper() in matched_txt:
                person = identifier
    return person
    
# Instance detection
def find_instances_xml(root, pattern_db, mp_db, classifier):
    """
    Find instances of segment start and end patterns in a txt file.

    Args:
        root: root of an lxml tree to be pattern matched.
        pattern_db: Patterns to be matched as a Pandas DataFrame.
    """
    columns = ['protocol_id', "elem_id", "pattern", "segmentation", "who", "id"]
    data = []
    protocol_id = root.attrib["id"]
    metadata = infer_metadata(protocol_id)
    pattern_rows = list(pattern_db.iterrows())
    
    mp_db = mp_db[mp_db["chamber"] == metadata["chamber"]]
    names = mp_db["name"]
    ids = mp_db["id"]
    names_ids = list(zip(names,ids))
    
    expressions = dict()
    manual = dict()
    for _, row in pattern_db.iterrows():
        if row["type"] == "regex":
            pattern = row['pattern']
            exp = re.compile(pattern)
            #Calculate digest for distringuishing patterns without ugly characters
            pattern_digest = hashlib.md5(pattern.encode("utf-8")).hexdigest()[:16]
            expressions[pattern_digest] = exp
        elif row["type"] == "manual":
            manual[row["pattern"]] = row["segmentation"]
    
    prot_speeches = dict()
    for content_block in root:
        cb_id = content_block.attrib["id"]
        content_txt = '\n'.join(content_block.itertext())
        if not _is_metadata_block(content_txt):
            for textblock in content_block:
                tb_id = textblock.attrib["id"]
                paragraph = textblock.text

                # Do not do segmentation if paragraph is empty
                if type(paragraph) != str:
                    continue

                for pattern, segmentation in manual.items():
                    if pattern in paragraph:
                        person = _detect_mp(matched_txt, names_ids)
                        #person = _detect_mp(matched_txt, names_ids)
                        d = {"protocol_id": protocol_id,
                            "pattern": "manual",
                            "segmentation": segmentation,
                            "elem_id": tb_id,
                            }
                        continue

                # Detect speaker introductions
                segmentation = None
                for pattern_digest, exp in expressions.items():
                    for m in exp.finditer(paragraph):
                        matched_txt = m.group()
                        person = _detect_mp(matched_txt, names_ids)
                        segmentation = "speech_start"
                        d = {
                        "protocol_id": protocol_id,
                        "pattern": pattern_digest,
                        "who": person,
                        "segmentation": segmentation,
                        "elem_id": tb_id,
                        }

                        data.append(d)

                        break

                # Do not do further segmentation if speech is detected
                if segmentation is not None:
                    continue

                # Use ML model to classify paragraph
                if classifier is not None:
                    preds = classify_paragraph(paragraph, classifier)
                    if np.argmax(preds) == 1:
                        segmentation = "note"
                        d = {
                        "protocol_id": protocol_id,
                        "segmentation": segmentation,
                        "elem_id": tb_id,
                        }

                        data.append(d)
        else:
            d = {"protocol_id": protocol_id, "pattern": None, "who": None, "segmentation": "metadata"}
            d["elem_id"] = cb_id
            data.append(d)

    return pd.DataFrame(data, columns=columns)

def apply_instances(protocol, instance_db):
    protocol_id = protocol.attrib["id"]
    
    applicable_instances = instance_db[instance_db["protocol_id"] == protocol_id]
    applicable_instances = applicable_instances.drop_duplicates(subset=['elem_id'])

    for _, row in applicable_instances.iterrows():
        elem_id = row["elem_id"]
        for target in protocol.xpath("//*[@id='" + elem_id + "']"):
            target.attrib["segmentation"] = row["segmentation"]
            if type(row["who"]) == str:
                target.attrib["who"] = row["who"]

            if type(row["id"]) == str:
                target.attrib["id"] = row["id"]

    return protocol
    
def find_instances(protocol_id, archive, pattern_db, mp_db, classifier=None):
    page_content_blocks = get_blocks(protocol_id, archive)
    instance_db = find_instances_xml(page_content_blocks, pattern_db, mp_db, classifier=classifier)
    
    instance_db["protocol_id"] = protocol_id
    return instance_db
    
def segmentation_workflow(file_db, archive, pattern_db, mp_db, ml=True):
    classifier = None
    if ml:
        import tensorflow as tf
        import fasttext.util

        model = tf.keras.models.load_model("segment-classifier")

        # Load word vectors from disk or download with the fasttext module
        vector_path = 'cc.sv.300.bin'
        fasttext.util.download_model('sv', if_exists='ignore')
        ft = fasttext.load_model(vector_path)

        classifier = dict(
            model=model,
            ft=ft,
            dim=ft.get_word_vector("hej").shape[0]
        )

    instance_dbs = []
    for corpus_year, package_ids, _ in year_iterator(file_db):
        print("Segmenting year:", corpus_year)
        
        year_patterns = filter_db(pattern_db, year=corpus_year)
        year_mps = filter_db(mp_db, year=corpus_year)
        
        for protocol_id in progressbar.progressbar(package_ids):
            protocol_patterns = filter_db(pattern_db, protocol_id=protocol_id)
            protocol_patterns = pd.concat([protocol_patterns, year_patterns])
            instance_db = find_instances(protocol_id, archive, protocol_patterns, year_mps, classifier=classifier)
            instance_dbs.append(instance_db)
    
    return pd.concat(instance_dbs)
