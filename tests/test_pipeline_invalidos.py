#!/usr/bin/env python3
"""Regresiones del #10: datos inválidos, identidad de variantes y salidas actuales.

VCF/TSV sintéticos, sin predicciones ni servicios. Requiere las dependencias de lectura
pysam, pandas y PyYAML; sale con 77 si faltan en el intérprete elegido por el runner.
"""
import contextlib
import csv
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'pipeline/bin'))
try:
    import pysam
    import pandas  # noqa: F401 — main() escribe el dossier con pandas
    import yaml  # noqa: F401 — configuración real, sin stub
except ImportError:
    print("SALTADO: test_pipeline_invalidos necesita pysam, pandas y PyYAML")
    sys.exit(77)
import correr_pipeline as cp
import leer_entradas as ent

U = dict(tpm_min=1.0, af_min=0.05, rank_presentacion=2.0)

class PipelineInvalidos(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix='issue10_synthetic_')
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.output = self.root/'dossier.tsv'
        self.manifest = Path(str(self.output)+'.manifiesto.json')
        self.hla = self.root/'hla.txt'; self.hla.write_text('')
        self.config = self.root/'thresholds.yaml'
        self.config.write_text('umbrales: {tpm_min: 1, af_min: 0.05, rank_presentacion: 2}\n')
        p = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        p.start(); self.addCleanup(p.stop)

    def variant(self, af=0.3, gene='SYN', pos=10):
        return dict(chrom='chrS',pos=pos,ref='C',alt='G',gene=gene,aa='changeA',peptide='SYNPEPTIDE',af=af)

    def vcf(self, records):
        header=pysam.VariantHeader()
        header.contigs.add('chrS')
        for key in ('GENE','AA','PEP'):
            header.info.add(key,1,'String','Synthetic annotation')
        header.formats.add('AF','A','Float','Synthetic fraction')
        header.add_sample('SAMPLE')
        path=self.root/'input.vcf'
        with pysam.VariantFile(str(path),'w',header=header) as out:
            for v in records:
                r=out.new_record(contig='chrS',start=v['pos']-1,alleles=('C','G'))
                for k,val in [('GENE',v['gene']),('AA',v['aa']),('PEP',v['peptide'])]:r.info[k]=val
                r.samples['SAMPLE']['AF']=(v['af'],)
                out.write(r)
        return path

    def expr(self, values):
        path=self.root/'expression.tsv'
        path.write_text('gene\ttpm\n'+''.join(f'{g}\t{v}\n' for g,v in values.items()))
        return path

    def run_cli(self, variants, expression=None, predict=False, structure=False):
        args=['correr_pipeline.py','--vcf',str(self.vcf(variants)),'--hla',str(self.hla),
               '--salida',str(self.output),'--config',str(self.config)]
        if predict:
            self.hla.write_text('HLA-A_SYNTHETIC\n')
        else:
            args.append('--no-presentacion')
        if structure:
            args.append('--estructura')
        if expression is not None:args.extend(['--expresion',str(self.expr(expression))])
        output=io.StringIO()
        with patch.object(sys,'argv',args),contextlib.redirect_stdout(output):rc=cp.main()
        return rc,output.getvalue()

    def test_invalid_numeric_filter_matrix(self):
        for field,values in [('af',[float('nan'),float('inf'),-float('inf'),-0.1,1.2,True,'bad']),
                             ('tpm',[float('nan'),float('inf'),-float('inf'),-1.0,True,'bad'])]:
            for value in values:
                with self.subTest(field=field,value=value):
                    v=self.variant(af=value if field=='af' else .3)
                    c,d=cp.filtrar([v],{'SYN':value if field=='tpm' else 10},U)
                    self.assertFalse(c);self.assertEqual(len(d),1)
                    self.assertIn('inválid',d[0][2]);self.assertIn(field.upper(),d[0][2])

    def test_absent_zero_and_thresholds(self):
        for af,tpm,expected in [(None,10,'incompleta'),(.3,None,'incompleta'),(None,None,'incompleta'),(.05,1,'completa'),(1,10,'completa')]:
            with self.subTest(af=af,tpm=tpm):
                c,d=cp.filtrar([self.variant(af)],{'SYN':tpm},U)
                self.assertEqual(c[0]['evaluacion'],expected);self.assertFalse(d)
        c,d=cp.filtrar([self.variant(0)],{'SYN':0},{**U,'tpm_min':0,'af_min':0})
        self.assertEqual(c[0]['evaluacion'],'completa');self.assertFalse(d)
        for af,tpm in [(0,10),(.3,0),(.04999,10),(.3,.9999)]:
            c,d=cp.filtrar([self.variant(af)],{'SYN':tpm},U)
            self.assertFalse(c);self.assertEqual(len(d),1);self.assertNotIn('inválid',d[0][2])

    def test_files_preserve_nonfinite_values_until_filter(self):
        for field in ('af','tpm'):
            for value in (float('nan'),float('inf'),-float('inf'),-1.0):
                with self.subTest(field=field,value=value):
                    vs=ent.leer_variantes(self.vcf([self.variant(value if field=='af' else .3)]))
                    expr=ent.leer_expresion(self.expr({'SYN':value if field=='tpm' else 10}))
                    c,d=cp.filtrar(vs,expr,U)
                    self.assertFalse(c);self.assertIn('inválid',d[0][2])

    def test_mixed_run_manifest_and_dossier(self):
        variants=[self.variant(.3,'GOOD',10),self.variant(float('nan'),'BADAF',20),
                  self.variant(.3,'BADTPM',30),self.variant(None,'ABSENT',40)]
        rc,output=self.run_cli(variants,{'GOOD':10,'BADAF':10,'BADTPM':'inf'})
        self.assertEqual(rc,0);self.assertIn('2 variante(s) con un valor PRESENTE pero inválido',output)
        m=json.loads(self.manifest.read_text())
        self.assertEqual(m['filas_completas'],1);self.assertEqual(m['filas_incompletas'],1)
        self.assertEqual(len(m['variantes_descartadas']),2)
        rows=list(csv.DictReader(io.StringIO(self.output.read_text()),delimiter='\t'))
        self.assertEqual([r['gene'] for r in rows],['GOOD','ABSENT'])
        self.assertEqual(rows[1]['evaluacion'],'incompleta')

    def test_all_invalid_records_persist_a_manifest(self):
        rc,output=self.run_cli([self.variant(float('nan'))],{'SYN':10})
        self.assertEqual(rc,0);self.assertIn('inválid',output)
        self.assertTrue(self.manifest.exists(),'all-invalid run loses its persistent discard record')
        m=json.loads(self.manifest.read_text())
        self.assertEqual((m['estado'],m['filas'],m['filas_completas'],m['filas_incompletas']),
                         ('sin_candidatos',0,0,0))
        self.assertEqual(len(m['variantes_descartadas']),1)
        self.assertIn('AF inválida',m['variantes_descartadas'][0]['motivo'])
        self.assertEqual(list(csv.DictReader(io.StringIO(self.output.read_text()),delimiter='\t')),[])

    def test_all_invalid_rerun_cannot_leave_old_success(self):
        self.run_cli([self.variant(.3)],{'SYN':10})
        old=self.output.read_bytes();old_manifest=self.manifest.read_bytes()
        rc,_=self.run_cli([self.variant(float('nan'))],{'SYN':10})
        unchanged=self.output.exists() and self.output.read_bytes()==old and self.manifest.read_bytes()==old_manifest
        self.assertFalse(rc==0 and unchanged,'successful exit leaves previous dossier and manifest unchanged')

    def test_malformed_tpm_is_not_absent(self):
        vs=ent.leer_variantes(self.vcf([self.variant()]))
        expr=ent.leer_expresion(self.expr({'SYN':'not-a-number'}))
        c,d=cp.filtrar(vs,expr,U)
        self.assertFalse(c,'present malformed TPM survived as missing/incomplete')
        self.assertEqual(len(d),1)
        self.assertIn('not-a-number',d[0][2])

    def test_distinct_loci_have_distinct_discard_records(self):
        vs=ent.leer_variantes(self.vcf([self.variant(float('nan'),pos=10),self.variant(float('nan'),pos=20)]))
        c,d=cp.filtrar(vs,{'SYN':10},U)
        self.assertFalse(c);self.assertEqual(len(d),2,'distinct loci collapse under (gene, aa)')


    def test_no_candidates_never_loads_predictors_or_structure(self):
        # Incluso solicitadas ambas etapas, un resultado vacío no carga modelos ni APIs.
        with patch.dict(sys.modules, {'presentacion_local':None, 'clientes_api':None}):
            rc,_=self.run_cli([self.variant(float('nan'))],{'SYN':10},predict=True,structure=True)
        self.assertEqual(rc,0)
        self.assertEqual(json.loads(self.manifest.read_text())['filas'],0)

    def test_empty_and_below_threshold_runs_replace_previous_outputs(self):
        for variants,expr in [([],{}),([self.variant(0)],{'SYN':10}),
                              ([self.variant(.3)],{'SYN':0})]:
            with self.subTest(variants=variants,expr=expr):
                self.run_cli([self.variant(.3)],{'SYN':10})
                rc,_=self.run_cli(variants,expr)
                self.assertEqual(rc,0)
                m=json.loads(self.manifest.read_text())
                self.assertEqual((m['estado'],m['filas']),('sin_candidatos',0))
                self.assertEqual(len(m['variantes_descartadas']),len(variants))
                self.assertEqual(list(csv.DictReader(io.StringIO(self.output.read_text()),delimiter='\t')),[])

    def test_peptides_share_one_variant_but_distinct_loci_do_not(self):
        bad=self.variant(float('nan'))
        good=self.variant(.3)
        for records,expr,n_desc,n_complete,n_incomplete in [
            ([bad,{**bad,'peptide':'ANOTHERPEP'}],{'SYN':10},1,0,0),
            ([good,{**good,'peptide':'ANOTHERPEP'},self.variant(.3,pos=20)],{'SYN':10},0,3,0),
            ([good,{**good,'peptide':'ANOTHERPEP'},self.variant(.3,pos=20)],{},0,0,3),
            ([bad,self.variant(float('nan'),pos=20)],{'SYN':10},2,0,0),
        ]:
            with self.subTest(n_desc=n_desc,n_complete=n_complete,n_incomplete=n_incomplete):
                rc,out=self.run_cli(records,expr)
                self.assertEqual(rc,0)
                m=json.loads(self.manifest.read_text())
                self.assertEqual(len(m['variantes_descartadas']),n_desc)
                self.assertEqual(m['filas_completas'],n_complete)
                self.assertEqual(m['filas_incompletas'],n_incomplete)
                if n_complete or n_incomplete:
                    self.assertIn('(2 variantes;',out)
                if n_incomplete:
                    self.assertIn('2 con evaluación INCOMPLETA',out)

    def test_malformed_tpm_in_runner_and_duplicate_expression_row(self):
        rc,out=self.run_cli([self.variant(),self.variant(gene='GOOD',pos=20)],
                            {'SYN':'not-a-number','GOOD':10})
        self.assertEqual(rc,0)
        self.assertIn('TPM inválido',out)
        m=json.loads(self.manifest.read_text())
        self.assertEqual((m['filas'],m['filas_completas']), (1,1))
        self.assertEqual(m['variantes_descartadas'][0]['gene'],'SYN')
        p=self.root/'duplicate.tsv'
        p.write_text('gene\ttpm\nSYN\t10\nSYN\tnot-a-number\n')
        c,d=cp.filtrar([self.variant()],ent.leer_expresion(p),U)
        self.assertFalse(c);self.assertIn('TPM inválido',d[0][2])

    def test_empty_tpm_fields_remain_absent(self):
        path=self.root/'empty.tsv'
        path.write_text('gene\ttpm\tnote\nSYN\t\tmissing\nBLANK\t   \tmissing\nNOFIELD\n')
        expr=ent.leer_expresion(path)
        self.assertEqual(expr,{})
        c,d=cp.filtrar([self.variant()],expr,U)
        self.assertFalse(d)
        self.assertEqual(c[0]['estado_expresion'],'no_medido')
        self.assertEqual(c[0]['evaluacion'],'incompleta')

if __name__=='__main__':
    unittest.main(verbosity=2)
