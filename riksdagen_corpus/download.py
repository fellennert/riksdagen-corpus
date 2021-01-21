import pandas as pd
import os, re
import shutil, getpass
import kblab
import progressbar
from PyPDF2 import PdfFileReader, PdfFileWriter
from lxml import etree
from riksdagen_corpus.utils import read_html

def login_to_archive():
    """
    Prompts the user for username and password, and logs in to KBLab. Returns the resulting KBLab client archive.
    """
    username = input("Username: ")
    password = getpass.getpass()
    print("Password set for user:", username)
    
    return kblab.Archive('https://betalab.kb.se', auth=(username, password))

def get_xml_blocks(xmlpath, htmlpath):
    xml_tree = etree.fromstring(open(xmlpath).read())
    html_tree = read_html(htmlpath)
    
    year = xml_tree.xpath(".//rm")[0].text
    protocol_number = xml_tree.xpath(".//nummer")[0].text
    protocol_id = "prot-" + year.replace("/", "") + "--" + protocol_number
    
    html_tree = html_tree.xpath(".//div[@class='Section1']")[0]
    root = etree.Element("protocol", id=protocol_id)
    
    cb_ix = 0
    tb_ix = 0
    contentBlock = etree.SubElement(root, "contentBlock", ix=str(cb_ix))
    for elem in html_tree:
        if elem.tag in ["p", "h1"]:
            elemtext = "".join(elem.itertext())
            
            linebreak = elemtext.strip() == "" and "\n" in elemtext
            if linebreak:
                tb_ix = 0
                cb_ix += 1
                contentBlock = etree.SubElement(root, "contentBlock", ix=str(cb_ix))
            else:
                textBlock = etree.SubElement(contentBlock, "textBlock", ix=str(tb_ix))
                tblock = elemtext.strip()
                tblock = tblock.replace("\n", " ")
                tblock = re.sub("\s+", " ", tblock)
                textBlock.text = tblock
                tb_ix += 1
    
    for xml_element in root.iter():
        content = xml_element.xpath('normalize-space()')
        if not content:
            parent = xml_element.getparent()
            if parent is not None:
                parent.remove(xml_element)
                    
    return root
    
def get_html_blocks(fpath):
    tree = read_html(fpath)
    id_class = "sidhuvud_beteckning"

    # Detect protocol id
    desc = None
    for div in tree.findall(".//div"):
        if "class" in div.attrib:
            classes = div.attrib["class"].split()
            if id_class in classes:
                desc = div.text

    if desc is not None:
        desc = re.sub('[^0-9:\\-]+', '', desc)
        desc = desc.replace(":", "--")
        desc = "prot-" + desc

        root = etree.Element("protocol", id=desc)
        
        # HTML structure with text formatted in <pre> blocks, roughly 1990-2003
        pres = tree.findall(".//pre")
        if len(pres) > 0:
            for ix, pre in enumerate(pres):
                contentBlock = etree.SubElement(root, "contentBlock", ix=str(ix))
                if pre.text is not None:
                    #contentBlock = etree.SubElement(contentBlock, "textBlock", ix=str(ix))
                    tblocks = re.sub('([a-zåäö,])- ?\n ?([a-zåäö])', '\\1\\2', pre.text)
                    tblocks = re.sub('([a-zåäö,]) ?\n ?([a-zåäö])', '\\1 \\2', tblocks)
                    
                    for tb_ix, tblock in enumerate(tblocks.split("\n")):
                        tblock = tblock.replace("\n", " ")
                        tblock = tblock.replace("\n", " ")
                        textBlock = etree.SubElement(contentBlock, "textBlock", ix=str(tb_ix))
                        textBlock.text = tblock
            
            return root
        
        # Standard HTML structure, roughly 2003-2013
        elif len(tree.xpath("//div[@class='indrag']")) > 0:
            
            tree = tree.xpath("//body")[0]
            
            cb_ix = 0
            tb_ix = 0
            contentBlock = etree.SubElement(root, "contentBlock", ix=str(cb_ix))
            for elem in tree:
                
                elemtext = "".join(elem.itertext())
                
                linebreak = elemtext.strip() == "" and "\n" in elemtext
                if elem.tag == "br" or linebreak:
                    tb_ix = 0
                    cb_ix += 1
                    contentBlock = etree.SubElement(root, "contentBlock", ix=str(cb_ix))
                else:
                    textBlock = etree.SubElement(contentBlock, "textBlock", ix=str(tb_ix))
                    tblock = elemtext.strip()
                    tblock = tblock.replace("\n", " ")
                    tblock = re.sub("\s+", " ", tblock)
                    textBlock.text = tblock
                    tb_ix += 1
            
            for xml_element in root.iter():
                content = xml_element.xpath('normalize-space()')
                if not content:
                    xml_element.getparent().remove(xml_element)
            
            return root
        else:
            return None

        
        
    else:
        return None

def get_blocks(package, package_id, load=True, save=True):
    """
    Get content and text blocks from an OCR output XML file. Concatenate words into sentences.

    Args:
        package: KBLab client package element
        package_id: ID of the package
        load: Load the file from disk if available
        save: Save the downloaded file to disk

    Returns an lxml elem tree with the structure page > contentBlock > textBlock.
    """
    #tree = etree.fromstring(s)
    
    folder = "data/protocols/" + package_id + "/"
    fname = "original.xml"
    overwrite = True
    if load or save:
        if not os.path.exists(folder):
            os.mkdir(folder)
    
    if load:
        fnames = os.listdir(folder)
        if fname in fnames:
            s = open(folder + fname).read()
            overwrite = False
            return etree.fromstring(s.encode("utf-8"))
    
    root = etree.Element("protocol", id=package_id)
    for ix, fname in enumerate(fetch_files(package)):
        s = package.get_raw(fname).read()
        tree = etree.fromstring(s)
        ns_dict = {"space": "http://www.loc.gov/standards/alto/ns-v3#"}
        content_blocks = tree.findall('.//{http://www.loc.gov/standards/alto/ns-v3#}ComposedBlock')
        
        for cb_ix, content_block in enumerate(content_blocks):
            content_block_e = etree.SubElement(root, "contentBlock", page=str(ix), ix=str(cb_ix))
            text_blocks = content_block.findall('.//{http://www.loc.gov/standards/alto/ns-v3#}TextBlock')
            for tb_ix, text_block in enumerate(text_blocks):
                tblock = []
                text_lines = text_block.findall('.//{http://www.loc.gov/standards/alto/ns-v3#}TextLine')
                
                for text_line in text_lines:
                    #tblock.append("\n")
                    strings = text_line.findall('.//{http://www.loc.gov/standards/alto/ns-v3#}String')
                    for string in strings:
                        content = string.attrib["CONTENT"]
                        tblock.append(content)
                    
                
                tblock = " ".join(tblock)
                # Remove line breaks when next line starts with a small letter
                tblock = re.sub('([a-zåäö,]) ?\n ?([a-zåäö])', '\\1 \\2', tblock)
                tblock = re.sub('([a-zåäö,])- ([a-zåäö])', '\\1\\2', tblock)
                text_block_e = etree.SubElement(content_block_e, "textBlock", ix=str(tb_ix))
                text_block_e.text = tblock
    
    if save and overwrite:
        fname = "original.xml"
        s = etree.tostring(root, pretty_print=True, encoding="utf-8", xml_declaration=True).decode("utf-8")
        f = open(folder + fname, "w")
        f.write(s)
        f.close()
        
    return root

def count_pages(start, end):
    years = range(start, end)
    archive = login_to_archive()
    
    rows = []
    
    for year in progressbar.progressbar(years):
        params = { 'tags': 'protokoll', 'meta.created': str(year)}
        package_ids = archive.search(params, max=365)
        
        for package_id in package_ids:
            package = archive.get(package_id)
            filelist = package.list()
            
            jp2list = [f for f in filelist if f.split(".")[-1] == "jp2"]
            page_count = len(jp2list)
            
            rows.append([package_id, year, page_count])
    
    columns = ["protocol_id", "year", "pages"]
    db_pages = pd.DataFrame(rows, columns=columns)
    return db_pages

def _create_dirs(outfolder):
    if not os.path.exists(outfolder):
        print("Create folder", outfolder)
        os.mkdir(outfolder)

    if not os.path.exists(outfolder + "train/"):
        os.mkdir(outfolder + "train/")

    if not os.path.exists(outfolder + "test/"):
        os.mkdir(outfolder + "test/")
    

def fetch_files(package, extension="xml"):
    """
    Fetch all files with the provided extension from a KBLab package

    Args:
        package: KBLab client package
        extension: File extension of the files that you want to fetch. String, or None which outputs all filetypes.
        return_files: Whether to return filenames or files zipped with filenames. Boolean, default value False returns just filenames.

    Depending on return_files, either outputs a list of filenames, or a list of file and filename tuples (String, String).
    """
    filelist = package.list()
    if extension is not None:
        filelist = [f for f in filelist if f.split(".")[-1] == extension]
    filelist = sorted(filelist)
    
    return filelist

def generate_sets(decade, interval=10, set_size=2, txt_dir=None):
    # Read pages dataframe, filter relevant data and sort
    total = 2 * set_size
    pages = pd.read_csv("db/protocols/pages.csv")
    pages_decade = pages[(pages["year"] >= decade) & (pages["year"] < decade + interval)]
    pages_decade = pages_decade.sort_values('ordinal')
    pages_decade = pages_decade.head(n=total)
    pages_decade = pages_decade.reset_index()

    print(pages_decade)
    
    # Create folder for the decennium
    outfolder = "data/curation/" + str(decade) + "-" + str(decade + interval-1) + "/"
    _create_dirs(outfolder)
    
    # Ask for credentials and establish connection 
    archive = login_to_archive()

    for ix, row in pages_decade.iterrows():
        package_id = row["package_id"]
        pagenumber = row["pagenumber"]
        print(ix, package_id, pagenumber)
        
        # Create folder for either train or test set
        folder = "train/"
        if ix % 2 == 1:
            folder = "test/"
        folder = outfolder + folder
        ix = ix // 2
    
        path = folder + str(ix) + "/"
        if not os.path.exists(path):
            print("Create folder", path)
            os.mkdir(path)
        
        # Write info.yaml
        info = open(path + "info.yaml", "w")
        info.write("package_id: " + package_id + "\n")
        info.write("pagenumber: " + str(pagenumber) + "\n")
        info.close()
        
        # Create empty original.txt and annotated.txt files
        original = open(path + "original.txt", "w")
        annotated = open(path + "annotated.txt", "w")
        original.close()
        annotated.close()
                
        # Download jp2 file and save it to disk
        package = archive.get(package_id)
        jp2list = fetch_files(package, extension="jp2")
        jp2numbers = [ int(f.split(".")[-2].split("-")[-1]) for f in jp2list]
        
        index = jp2numbers.index(pagenumber)        
        jp2file = jp2list[index]
        imagedata = package.get_raw(jp2file).read()
        
        jp2out = open(path + "image.jp2", "wb")
        jp2out.write(imagedata)
        jp2out.close()
        
        if txt_dir is not None:
            txt_filename = jp2file.split("-")[0] + ".txt"
            txt = open(txt_dir + txt_filename).read()
            
            txtout = open(path + txt_filename, "w")
            txtout.write(txt)
            txtout.close()

def _get_seed(string):
    encoded = string.encode('utf-8')
    digest = hashlib.md5(encoded).hexdigest()[:8]
    return int(digest, 16)

def randomize_ordinals(files):
    columns = ["package_id", "year", "pagenumber", "ordinal"]
    data = []
    for index, row in files.iterrows():
        #print(index, row)
        package_id = row["package_id"]
        pages = row["pages"]
        year = row["year"]

        for page in range(0, pages):

            seedstr = package_id + str(year) + str(page)
            np.random.seed(_get_seed(seedstr))
            ordinal = np.random.rand()
            new_row = [package_id, year, page, ordinal]
            data.append(new_row)

    return pd.DataFrame(data, columns = columns)
