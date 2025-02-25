
import logging
import pathlib
import json
import re
from itertools import chain
from collections import defaultdict

from .. import PnR
from ..schema.hacks import VerilogJsonTop

logger = logging.getLogger(__name__)

NType = PnR.NType
Omark = PnR.Omark

def _ReadVerilogJson( DB, j, add_placement_info=False):
    hierTree = []

    for module in j['modules']:

        temp_node = PnR.hierNode()
        temp_node.name = module['name'] if 'name' in module else module['abstract_name']
        temp_node.isCompleted = 0

        Terminals = []
        for parameter in module['parameters']:
            temp_terminal = PnR.terminal()
            temp_terminal.name = parameter
            temp_terminal.type = 'input' # All nets are inputs, we don't use this for anything do we?
            Terminals.append( temp_terminal)
        temp_node.Terminals = Terminals

        terminal_map = { term.name : term for term in temp_node.Terminals}
        net_map = {}

        Blocks = []
        Nets = []
        Block_name_map = {}

        Connecteds = []

        for instance in module['instances']:
            temp_blockComplex = PnR.blockComplex()
            current_instance = PnR.block()

            if 'template_name' in instance:
                current_instance.master = instance['template_name']
            elif 'abstract_template_name' in instance:
                current_instance.master = instance['abstract_template_name']
            else:
                assert False, f'Missing template_name (abstract or otherwise) in instance {instance}'

            current_instance.name = instance['instance_name']
            Block_name_map[current_instance.name] = len(Blocks)

            blockPins = []

            def process_connection( iter, net_name):
                net_index = 0
                if net_name in net_map:
                    net_index = net_map[net_name]
                else:
                    net_index = len(Nets)
                    Nets.append( PnR.net())
                    Connecteds.append( [])
                    Nets[-1].name = net_name

                    net_map[net_name] = net_index

                # Use a python list of list to workaround not being able to append to a C++ vector
                Connecteds[net_index].append( PnR.connectNode())
                Connecteds[net_index][-1].type = PnR.Block
                Connecteds[net_index][-1].iter = iter
                Connecteds[net_index][-1].iter2 = len(Blocks)

                return net_index


            for i,fa in enumerate(instance['fa_map']):
                temp_pin = PnR.pin()
                temp_pin.name = fa['formal']
                net_name = fa['actual']
                temp_pin.netIter = process_connection( i, net_name)
                blockPins.append( temp_pin)

            current_instance.blockPins = blockPins
            temp_blockComplex.instance = [ current_instance ]
            Blocks.append( temp_blockComplex)

        for net,connected in zip(Nets,Connecteds):
            net.connected = connected
            net.degree = len(connected)

        temp_node.Blocks = Blocks
        temp_node.Nets = Nets
        temp_node.Block_name_map = Block_name_map

        hierTree.append( temp_node)

    DB.hierTree = hierTree

    global_signals = []
    for global_signal in j['global_signals']:
        global_signals.append( (global_signal['prefix'], global_signal['formal'], global_signal['actual']))

    return global_signals

def _ReadMap(path, mapname):
    d = pathlib.Path(path)
    p = re.compile( r'^(\S+)\s+(\S+)\s*$')
    with (d / mapname).open( "rt") as fp:
        for line in fp:
            line = line.rstrip('\n')
            m = p.match(line)
            assert m
            k, v = m.groups()
            yield k, str(d/v)

def _ConstructMap(pairs):
    tbl2 = defaultdict(list)
    for k, v in pairs:
        tbl2[k].append(v)
    return tbl2

def _attach_constraint_files( DB, fpath):
    d = pathlib.Path(fpath)

    for curr_node in DB.hierTree:
        curr_node.bias_Vgraph  = DB.getDrc_info().Design_info.Vspace
        curr_node.bias_Hgraph  = DB.getDrc_info().Design_info.Hspace
        curr_node.compact_style = DB.getDrc_info().Design_info.compact_style

        fp = d / f"{curr_node.name}.pnr.const.json"
        if fp.exists():
            with fp.open("rt") as fp:
                jsonStr = fp.read()
            logger.debug(f"Reading contraint json file {curr_node.name}.pnr.const.json:\n{jsonStr}")
            DB.ReadConstraint_Json(curr_node, jsonStr)
            logger.debug(f"Finished reading contraint json file {curr_node.name}.pnr.const.json")
        else:
            logger.warning(f"No constraint file for module {curr_node.name}")

    for name, instances in DB.lefData.items():
        fp = d / f"{name}.json"
        if fp.exists():
            with fp.open( "rt") as fp:
                jsonStr = fp.read()
            DB.ReadPrimitiveOffsetPitch(instances, jsonStr)
            logger.debug(f"Finished reading primitive json file {name}.json")
        else:
            logger.warning(f"No primitive json file for primitive {name}. Okay if a CC capacitor.")

def _semantic(DB, path, topcell, global_signals):
    _attach_constraint_files( DB, path)
    DB.semantic0( topcell)
    DB.semantic1( global_signals)
    DB.semantic2()

def PnRdatabase( path, topcell, vname, lefname, mapname, drname, *, verilog_d_in=None, map_d_in=None, lef_s_in=None):
    DB = PnR.PnRdatabase()

    assert drname.endswith('.json'), drname
    DB.ReadPDKJSON( path + '/' + drname)

    if lef_s_in is not None:
        logger.info(f'Reading LEF from string...')
        DB.ReadLEFFromString(lef_s_in)
    else:
        p = pathlib.Path(path) / lefname
        if p.exists():
            with p.open( "rt") as fp:
                DB.ReadLEFFromString(fp.read())
        else:
            logger.warn(f"LEF file {p} doesn't exist.")


    if map_d_in is None:
        DB.gdsData2 = _ConstructMap(_ReadMap(path, mapname))
    else:
        DB.gdsData2 = _ConstructMap(map_d_in)

    if verilog_d_in is None:
        with (pathlib.Path(path) / vname).open("rt") as fp:
            verilog_d = VerilogJsonTop.parse_obj(json.load(fp=fp))
    else:
        verilog_d = verilog_d_in

    global_signals = _ReadVerilogJson( DB, verilog_d)
    _semantic(DB, path, topcell, global_signals)

    return DB, verilog_d

def gen_DB_verilog_d(toplevel_args_d, results_dir, *, verilog_d_in=None, map_d_in=None, lef_s_in=None):
    fpath = toplevel_args_d['input_dir']
    lfile = toplevel_args_d['lef_file']
    vfile = toplevel_args_d['verilog_file']
    mfile = toplevel_args_d['map_file']
    dfile = toplevel_args_d['pdk_file']
    topcell = toplevel_args_d['subckt']
    numLayout = toplevel_args_d['nvariants']
    effort = toplevel_args_d['effort']

    DB, verilog_d = PnRdatabase( fpath, topcell, vfile, lfile, mfile, dfile, verilog_d_in=verilog_d_in, map_d_in=map_d_in, lef_s_in=lef_s_in)

    assert verilog_d is not None

    if results_dir is None:
        opath = './Results/'
    else:
        opath = str(pathlib.Path(results_dir))
        if opath[-1] != '/':
            opath = opath + '/'

    pathlib.Path(opath).mkdir(parents=True,exist_ok=True)

    return DB, verilog_d, fpath, opath, numLayout, effort
