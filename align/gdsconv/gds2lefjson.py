#!/usr/bin/env python

import gdspy
import json
import argparse
import os
import shutil

from align.gdsconv.gds2json import convert_GDS_GDSjson


class GDS2_LEF_JSON:
    def __init__(self, layerfile, gdsfile, name):
        (self._layers, self._layernames) = self.readLayerInfo(layerfile)
        self._cell     = self.readGDS(gdsfile)
        self._cellname = name if name else (self._cell.name if self._cell else gdsfile[(gdsfile.find('/') + 1):gdsfile.find('.gds')])
        self._units    = gdspy.get_gds_units(gdsfile)[1]
        self._gdsfile  = gdsfile

    def readLayerInfo(self, layerfile):
        layers = dict()
        layernames = dict()
        with open(layerfile) as fp:
            layerdata = json.load(fp)
            if "Abstraction" in layerdata:
                for l in layerdata["Abstraction"]:
                    if "Layer" in l and "GdsLayerNo" in l:
                        layer = l["Layer"]
                        glno1 = l["GdsLayerNo"]
                        glno2 = dict()
                        layernames[glno1] = layer
                        if "GdsDatatype" in l:
                            for key, idx in l["GdsDatatype"].items():
                                glno2[idx] = key
                        layers[layer] = glno2
        return (layers, layernames)
    
    def readGDS(self, gdsfile):
        cell = None
        if not os.path.isfile(gdsfile):
            print(f'leaf {gdsfile} not found')
            exit()
        lib = gdspy.GdsLibrary(infile=gdsfile)
        cell = lib.top_level()[0]
        cell.flatten()
        return cell
    
    def writeLEFJSON(self, outdir):
        if not self._cell: return
        leffile = self._cellname + '.lef'
        plleffile = self._cellname + '.placement_lef'
        bbox = self._cell.get_bounding_box() * 1e9
        dim = [round((bbox[1][0] - bbox[0][0])), round((bbox[1][1] - bbox[0][1]))]
        jsondict = dict()
        jsondict["bbox"] = [round(bbox[i][j]) for i in (0,1) for j in (0,1)]
        jsondict["globalRoutes"] = []
        jsondict["globalRouteGrid"] = []
        jsondict["terminals"] = []
        with open(outdir + leffile, 'wt') as ofs:
             #print(f'Writing LEF file : {leffile}')
            ofs.write(f'MACRO {self._cellname}\n')
            ofs.write(f'  UNITS\n    DATABASE MICRONS UNITS {round(1e-6/self._units)};\n  END UNITS\n')
            ofs.write(f'  ORIGIN {bbox[0][0]} {bbox[0][1]} ;\n')
            ofs.write(f'  FOREIGN {self._cellname} {bbox[0][0]} {bbox[0][1]} ;\n')
            ofs.write(f'  SIZE {dim[0]} BY {dim[1]} ;\n')
            polygons = self._cell.get_polygons(True)
            pincache = set()
            for lbl in self._cell.get_labels():
                if lbl.layer in self._layernames:
                    lname = self._layernames[lbl.layer]
                    pos = lbl.position * 1e9
                    if lname in self._layers:
                        pinidx = None
                        for idx, k in self._layers[lname].items():
                            if k == 'Pin':
                                pinidx = idx
                                break
                        key = (lbl.layer, pinidx)
                        if key in polygons:
                            for poly in polygons[key]:
                                if len(poly) < 2: continue
                                box = [round(min(r[0] for r in poly) * 1e9), round(min(r[1] for r in poly) * 1e9),
                                       round(max(r[0] for r in poly) * 1e9), round(max(r[1] for r in poly) * 1e9)]
                                if box[0] <= pos[0] and box[1] <= pos[1] and box[2] >= pos[0] and box[3] >= pos[1]:
                                    ofs.write(f'  PIN {lbl.text}\n    DIRECTION INOUT ;\n    USE SIGNAL ;\n    PORT\n')
                                    ofs.write(f'      LAYER {lname} ;\n')
                                    ofs.write(f'        RECT {box[0]} {box[1]} {box[2]} {box[3]} ;\n')
                                    ofs.write(f'    END\n  END {lbl.text}\n')
                                    pindict = {"layer": lname, "netName": lbl.text, "rect": box, "netType": "pin"}
                                    jsondict["terminals"].append(pindict)
                                    pincache.add(str([key, box]))
                        drawidx = None
                        for idx, k in self._layers[lname].items():
                            if k == 'Draw':
                                drawinidx = idx
                                break
                        key = (lbl.layer, drawinidx)
                        if key in polygons:
                            for poly in polygons[key]:
                                if len(poly) < 2: continue
                                box = [round(min(r[0] for r in poly) * 1e9), round(min(r[1] for r in poly) * 1e9),
                                       round(max(r[0] for r in poly) * 1e9), round(max(r[1] for r in poly) * 1e9)]
                                if box[0] <= pos[0] and box[1] <= pos[1] and box[2] >= pos[0] and box[3] >= pos[1]:
                                    pindict = {"layer": lname, "netName": lbl.text, "rect": box, "netType": "drawing"}
                                    jsondict["terminals"].append(pindict)
                                    pincache.add(str([key, box]))
                        
            ofs.write('  OBS\n')
            for k in polygons:
                if k[0] not in self._layernames: continue
                lname = self._layernames[k[0]]
                if lname not in self._layers or k[1] not in self._layers[lname] or lname.lower() == 'bbox': continue
                for poly in polygons[k]:
                    if len(poly) < 2: continue
                    box = [ round(min(r[0] for r in poly) * 1e9), round(min(r[1] for r in poly) * 1e9),
                        round(max(r[0] for r in poly) * 1e9), round(max(r[1] for r in poly) * 1e9) ]
                    if 'M' in lname or 'V' in lname and (self._layers[lname][k[1]].lower() not in ('label')):
                        if str([k, box]) not in pincache:
                            ofs.write(f'    LAYER {lname} ;\n      RECT {box[0]} {box[1]} {box[2]} {box[3]} ;\n')
                            shapedict = {"layer": lname, "netName": None, "rect": box, "netType": "drawing"}
                    else: shapedict = {"netName": None, "layer": lname, "rect": box, "netType": "drawing"}
                    jsondict["terminals"].append(shapedict)
            ofs.write('  END\n')
            ofs.write(f'END {self._cellname}\n')
        jsonfn = self._cellname + '.json'
        with open(outdir + jsonfn, 'wt') as fp:
             #print(f'Writing JSON file : {jsonfn}')
            json.dump(jsondict, fp, indent = 2)
         #print(f'Writing PLACEMENT_LEF file : {plleffile}')
        shutil.copy(outdir + leffile, outdir + plleffile)
         #print(f'Writing GDS.JSON file : {self._cellname}.gds.json')
        convert_GDS_GDSjson(self._gdsfile, outdir + self._cellname + '.gds.json')
         #print('--')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument( "-g", "--gds",    type=str, default="", help='<gds file>')
    ap.add_argument( "-l", "--layers", type=str, default="", help='<layers.json file>')
    ap.add_argument( "-o", "--outdir", type=str, default="", help="<file output directory>")
    ap.add_argument( "-n", "--name",   type=str, default="", help="<name to use for output module>")
    args = ap.parse_args()
    
    if args.layers == "" or args.gds == "":
        ap.print_help()
        exit(0)
    else:
        if args.outdir and args.outdir[-1] != '/':
            args.outdir += '/'
        
        print(f"gds file     : {args.gds}")
        print(f"layers       : {args.layers}")
        print(f"output dir   : {args.outdir if args.outdir else './'}")
        gds2lef = GDS2_LEF_JSON(args.layers, args.gds, args.name)
        gds2lef.writeLEFJSON(args.outdir)
