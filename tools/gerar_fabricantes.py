# Turns the public IEEE manufacturer list into the small table the panel reads (fabricantes.tsv).
#
# 1. Download oui.csv from https://standards-oui.ieee.org/oui/oui.csv into an empty folder.
# 2. Run:  python -I tools/gerar_fabricantes.py <path to oui.csv> fabricantes.tsv
#
# Each output line is the first six hex digits of the MAC, a tab, and a short manufacturer name.
import csv
import re
import sys

TAIL = re.compile(r'[ .]+(co|ltd|inc|corp|corporation|limited|llc|gmbh|s\.?a|company|technology|technologies|electronics?|'
                  r'international|communications?|systems?|group|holdings?|industrial|trading)\.?$', re.I)


def short(name):
    n = ' '.join(name.split())
    head = n.split(',')[0].strip()
    if len(head) >= 3:
        n = head
    for _ in range(4):
        cut = TAIL.sub('', n).strip(' .')
        if cut == n or len(cut) < 3:
            break
        n = cut
    if n.isupper() and len(n) > 5 and re.match(r'^[A-Z ]+$', n):
        n = n.title()
    return n[:34]


def main(src, dst):
    table = {}
    with open(src, encoding='utf-8', errors='replace', newline='') as f:
        for row in csv.reader(f):
            if len(row) < 3 or not re.match(r'^[0-9A-Fa-f]{6}$', row[1]):
                continue
            name = short(row[2])
            # Blocks handed out in smaller pieces belong to many makers; the big list only names the registry.
            if not name or name.lower().startswith(('ieee registration', 'private')):
                continue
            table[row[1].upper()] = re.sub(r'[\t\r\n]', ' ', name)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        for prefix in sorted(table):
            f.write('%s\t%s\n' % (prefix, table[prefix]))
    print('%d fabricantes gravados em %s' % (len(table), dst))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
