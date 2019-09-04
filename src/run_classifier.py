# coding=utf-8
# This file is based on https://github.com/google-research/bert/blob/master/run_classifier.py.
# It is changed to use SentencePiece tokenizer and https://www.rondhuit.com/download/ldcc-20140209.tar.gz.
"""BERT finetuning runner."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import configparser
import csv
import json
import os
import sys
import tempfile
import tokenization_sentencepiece as tokenization
import tensorflow as tf
import utils

CURDIR = os.path.dirname(os.path.abspath(__file__))
CONFIGPATH = os.path.join(CURDIR, os.pardir, 'config.ini')
config = configparser.ConfigParser()
config.read(CONFIGPATH)
bert_config_file = tempfile.NamedTemporaryFile(mode='w+t', encoding='utf-8', suffix='.json')
bert_config_file.write(json.dumps({k:utils.str_to_value(v) for k,v in config['BERT-CONFIG'].items()}))
bert_config_file.seek(0)

sys.path.append(os.path.join(CURDIR, os.pardir, 'bert'))
import modeling
import optimization

flags = tf.flags

FLAGS = flags.FLAGS

# Required parameters
flags.DEFINE_string(
    "data_dir", None,
    "The input data dir. Should contain the .tsv files (or other data files) "
    "for the task.")

flags.DEFINE_string(
    "bert_config_file", None,
    "The config json file corresponding to the pre-trained BERT model. "
    "This specifies the model architecture.")

flags.DEFINE_string("task_name", None, "The name of the task to train.")

flags.DEFINE_string("model_file", None,
                    "The model file that the SentencePiece model was trained on.")

flags.DEFINE_string("vocab_file", None,
                    "The vocabulary file that the BERT model was trained on.")

flags.DEFINE_string(
    "output_dir", None,
    "The output directory where the model checkpoints will be written.")

# Other parameters

flags.DEFINE_string(
    "init_checkpoint", None,
    "Initial checkpoint (usually from a pre-trained BERT model).")

flags.DEFINE_bool(
    "do_lower_case", True,
    "Whether to lower case the input text. Should be True for uncased "
    "models and False for cased models.")

flags.DEFINE_integer(
    "max_seq_length", 128,
    "The maximum total input sequence length after WordPiece tokenization. "
    "Sequences longer than this will be truncated, and sequences shorter "
    "than this will be padded.")

flags.DEFINE_bool("do_train", False, "Whether to run training.")

flags.DEFINE_bool("do_eval", False, "Whether to run eval on the dev set.")

flags.DEFINE_bool(
    "do_predict", False,
    "Whether to run the model in inference mode on the test set.")

flags.DEFINE_integer("train_batch_size", 32, "Total batch size for training.")

flags.DEFINE_integer("eval_batch_size", 8, "Total batch size for eval.")

flags.DEFINE_integer("predict_batch_size", 8, "Total batch size for predict.")

flags.DEFINE_float("learning_rate", 5e-5, "The initial learning rate for Adam.")

flags.DEFINE_float("num_train_epochs", 3.0,
                   "Total number of training epochs to perform.")

flags.DEFINE_float(
    "warmup_proportion", 0.1,
    "Proportion of training to perform linear learning rate warmup for. "
    "E.g., 0.1 = 10% of training.")

flags.DEFINE_integer("save_checkpoints_steps", 1000,
                     "How often to save the model checkpoint.")

flags.DEFINE_integer("iterations_per_loop", 1000,
                     "How many steps to make in each estimator call.")

flags.DEFINE_bool("use_tpu", False, "Whether to use TPU or GPU/CPU.")

tf.flags.DEFINE_string(
    "tpu_name", None,
    "The Cloud TPU to use for training. This should be either the name "
    "used when creating the Cloud TPU, or a grpc://ip.address.of.tpu:8470 "
    "url.")

tf.flags.DEFINE_string(
    "tpu_zone", None,
    "[Optional] GCE zone where the Cloud TPU is located in. If not "
    "specified, we will attempt to automatically detect the GCE project from "
    "metadata.")

tf.flags.DEFINE_string(
    "gcp_project", None,
    "[Optional] Project name for the Cloud TPU-enabled project. If not "
    "specified, we will attempt to automatically detect the GCE project from "
    "metadata.")

tf.flags.DEFINE_string("master", None, "[Optional] TensorFlow master URL.")

flags.DEFINE_integer(
    "num_tpu_cores", 8,
    "Only used if `use_tpu` is True. Total number of TPU cores to use.")


class InputExample(object):
  """A single training/test example for simple sequence classification."""

  def __init__(self, guid, text_a, text_b=None, label=None):
    """Constructs a InputExample.

    Args:
      guid: Unique id for the example.
      text_a: string. The untokenized text of the first sequence. For single
        sequence tasks, only this sequence must be specified.
      text_b: (Optional) string. The untokenized text of the second sequence.
        Only must be specified for sequence pair tasks.
      label: (Optional) string. The label of the example. This should be
        specified for train and dev examples, but not for test examples.
    """
    self.guid = guid
    self.text_a = text_a
    self.text_b = text_b
    self.label = label


class PaddingInputExample(object):
  """Fake example so the num input examples is a multiple of the batch size.

  When running eval/predict on the TPU, we need to pad the number of examples
  to be a multiple of the batch size, because the TPU requires a fixed batch
  size. The alternative is to drop the last batch, which is bad because it means
  the entire output data won't be generated.

  We use this class instead of `None` because treating `None` as padding
  battches could cause silent errors.
  """


class InputFeatures(object):
  """A single set of features of data."""

  def __init__(self,
               input_ids,
               input_mask,
               segment_ids,
               label_id,
               is_real_example=True):
    self.input_ids = input_ids
    self.input_mask = input_mask
    self.segment_ids = segment_ids
    self.label_id = label_id
    self.is_real_example = is_real_example


class DataProcessor(object):
  """Base class for data converters for sequence classification data sets."""

  def get_train_examples(self, data_dir):
    """Gets a collection of `InputExample`s for the train set."""
    raise NotImplementedError()

  def get_dev_examples(self, data_dir):
    """Gets a collection of `InputExample`s for the dev set."""
    raise NotImplementedError()

  def get_test_examples(self, data_dir):
    """Gets a collection of `InputExample`s for prediction."""
    raise NotImplementedError()

  def get_labels(self):
    """Gets the list of labels for this data set."""
    raise NotImplementedError()

  @classmethod
  def _read_tsv(cls, input_file, quotechar=None):
    """Reads a tab separated value file."""
    with tf.gfile.Open(input_file, "r") as f:
      reader = csv.reader(f, delimiter="\t", quotechar=quotechar)
      lines = []
      for line in reader:
        lines.append(line)
      return lines


class LivedoorProcessor(DataProcessor):
  """Processor for the livedoor data set (see https://www.rondhuit.com/download.html)."""

  def get_train_examples(self, data_dir):
    """See base class."""
    return self._create_examples(
        self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

  def get_dev_examples(self, data_dir):
    """See base class."""
    return self._create_examples(
        self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

  def get_test_examples(self, data_dir):
    """See base class."""
    return self._create_examples(
        self._read_tsv(os.path.join(data_dir, "test.tsv")), "test")

  def get_labels(self):
    """See base class."""
    return ['10006',\
'11002',\
'12025',\
'12033',\
'12041',\
'12050',\
'12068',\
'12076',\
'12084',\
'12092',\
'12106',\
'12114',\
'12122',\
'12131',\
'12149',\
'12157',\
'12165',\
'12173',\
'12181',\
'12190',\
'12203',\
'12211',\
'12220',\
'12238',\
'12246',\
'12254',\
'12262',\
'12271',\
'12289',\
'12297',\
'12301',\
'12319',\
'12335',\
'12343',\
'12351',\
'12360',\
'13030',\
'13048',\
'13315',\
'13323',\
'13331',\
'13340',\
'13374',\
'13439',\
'13455',\
'13463',\
'13471',\
'13617',\
'13625',\
'13633',\
'13641',\
'13676',\
'13706',\
'13714',\
'13919',\
'13927',\
'13935',\
'13943',\
'13951',\
'13960',\
'13978',\
'13986',\
'13994',\
'14001',\
'14010',\
'14028',\
'14036',\
'14044',\
'14052',\
'14061',\
'14079',\
'14087',\
'14095',\
'14231',\
'14249',\
'14257',\
'14273',\
'14281',\
'14290',\
'14303',\
'14311',\
'14320',\
'14338',\
'14346',\
'14362',\
'14371',\
'14389',\
'14524',\
'14532',\
'14541',\
'14559',\
'14567',\
'14575',\
'14583',\
'14591',\
'14605',\
'14613',\
'14621',\
'14630',\
'14648',\
'14656',\
'14681',\
'14699',\
'14702',\
'14711',\
'14729',\
'14818',\
'14826',\
'14834',\
'14842',\
'14851',\
'14869',\
'14877',\
'15113',\
'15121',\
'15130',\
'15148',\
'15164',\
'15172',\
'15181',\
'15199',\
'15202',\
'15431',\
'15440',\
'15458',\
'15466',\
'15474',\
'15491',\
'15504',\
'15521',\
'15555',\
'15598',\
'15601',\
'15610',\
'15628',\
'15636',\
'15644',\
'15717',\
'15750',\
'15784',\
'15814',\
'15849',\
'15857',\
'15865',\
'16012',\
'16021',\
'16047',\
'16071',\
'16080',\
'16098',\
'16101',\
'16314',\
'16322',\
'16331',\
'16349',\
'16357',\
'16365',\
'16373',\
'16381',\
'16390',\
'16411',\
'16420',\
'16438',\
'16446',\
'16454',\
'16462',\
'16471',\
'16489',\
'16497',\
'16616',\
'16624',\
'16632',\
'16641',\
'16659',\
'16675',\
'16683',\
'16918',\
'16926',\
'16934',\
'16942',\
'20001',\
'22012',\
'22021',\
'22039',\
'22047',\
'22055',\
'22063',\
'22071',\
'22080',\
'22098',\
'22101',\
'23019',\
'23035',\
'23043',\
'23078',\
'23213',\
'23230',\
'23434',\
'23612',\
'23621',\
'23671',\
'23817',\
'23841',\
'23876',\
'24015',\
'24023',\
'24058',\
'24066',\
'24082',\
'24112',\
'24121',\
'24236',\
'24244',\
'24252',\
'24261',\
'24414',\
'24422',\
'24431',\
'24457',\
'24465',\
'24503',\
'30007',\
'32018',\
'32026',\
'32034',\
'32051',\
'32069',\
'32077',\
'32085',\
'32093',\
'32107',\
'32115',\
'32131',\
'32140',\
'32158',\
'32166',\
'33014',\
'33022',\
'33031',\
'33219',\
'33227',\
'33669',\
'33812',\
'34029',\
'34410',\
'34614',\
'34827',\
'34835',\
'34843',\
'34851',\
'35017',\
'35033',\
'35068',\
'35076',\
'35246',\
'40002',\
'41009',\
'42021',\
'42030',\
'42056',\
'42064',\
'42072',\
'42081',\
'42099',\
'42111',\
'42129',\
'42137',\
'42145',\
'42153',\
'42161',\
'43010',\
'43028',\
'43214',\
'43222',\
'43231',\
'43249',\
'43419',\
'43613',\
'43621',\
'44016',\
'44041',\
'44067',\
'44211',\
'44229',\
'44245',\
'44440',\
'44458',\
'45012',\
'45055',\
'45811',\
'46060',\
'50008',\
'52019',\
'52027',\
'52035',\
'52043',\
'52060',\
'52078',\
'52094',\
'52108',\
'52116',\
'52124',\
'52132',\
'52141',\
'52159',\
'53031',\
'53279',\
'53465',\
'53481',\
'53490',\
'53619',\
'53635',\
'53660',\
'53686',\
'54348',\
'54631',\
'54640',\
'60003',\
'62014',\
'62022',\
'62031',\
'62049',\
'62057',\
'62065',\
'62073',\
'62081',\
'62090',\
'62103',\
'62111',\
'62120',\
'62138',\
'63011',\
'63029',\
'63215',\
'63223',\
'63231',\
'63240',\
'63410',\
'63614',\
'63622',\
'63631',\
'63649',\
'63657',\
'63665',\
'63673',\
'63819',\
'63827',\
'64017',\
'64025',\
'64033',\
'64262',\
'64289',\
'64611',\
'70009',\
'72010',\
'72028',\
'72036',\
'72044',\
'72052',\
'72079',\
'72087',\
'72095',\
'72109',\
'72117',\
'72125',\
'72133',\
'72141',\
'73016',\
'73032',\
'73083',\
'73229',\
'73423',\
'73440',\
'73628',\
'73644',\
'73679',\
'73687',\
'74021',\
'74055',\
'74071',\
'74080',\
'74217',\
'74225',\
'74233',\
'74446',\
'74454',\
'74462',\
'74471',\
'74616',\
'74641',\
'74659',\
'74667',\
'74811',\
'74829',\
'74837',\
'74845',\
'75019',\
'75027',\
'75035',\
'75043',\
'75051',\
'75213',\
'75221',\
'75418',\
'75426',\
'75434',\
'75442',\
'75451',\
'75469',\
'75477',\
'75485',\
'75612',\
'75647',\
'80004',\
'82015',\
'82023',\
'82031',\
'82040',\
'82058',\
'82074',\
'82082',\
'82104',\
'82112',\
'82121',\
'82147',\
'82155',\
'82163',\
'82171',\
'82198',\
'82201',\
'82210',\
'82228',\
'82236',\
'82244',\
'82252',\
'82261',\
'82279',\
'82287',\
'82295',\
'82309',\
'82317',\
'82325',\
'82333',\
'82341',\
'82350',\
'82368',\
'83020',\
'83097',\
'83101',\
'83411',\
'83640',\
'84425',\
'84433',\
'84476',\
'85219',\
'85421',\
'85464',\
'85642',\
'90000',\
'92011',\
'92029',\
'92037',\
'92045',\
'92053',\
'92061',\
'92088',\
'92096',\
'92100',\
'92118',\
'92134',\
'92142',\
'92151',\
'92169',\
'93017',\
'93424',\
'93432',\
'93441',\
'93459',\
'93611',\
'93645',\
'93840',\
'93866',\
'94072',\
'94111',\
'100005'\,
'102016'\,
'102024'\,
'102032'\,
'102041'\,
'102059'\,
'102067'\,
'102075'\,
'102083'\,
'102091'\,
'102105'\,
'102113'\,
'102121'\,
'103446'\,
'103454'\,
'103667'\,
'103675'\,
'103829'\,
'103837'\,
'103845'\,
'104213'\,
'104248'\,
'104256'\,
'104264'\,
'104281'\,
'104299'\,
'104434'\,
'104442'\,
'104485'\,
'104493'\,
'104647'\,
'105210'\,
'105228'\,
'105236'\,
'105244'\,
'105252'\,
'110001'\,
'111007'\,
'112011'\,
'112020'\,
'112038'\,
'112062'\,
'112071'\,
'112089'\,
'112097'\,
'112101'\,
'112119'\,
'112127'\,
'112143'\,
'112151'\,
'112160'\,
'112178'\,
'112186'\,
'112194'\,
'112216'\,
'112224'\,
'112232'\,
'112241'\,
'112259'\,
'112275'\,
'112283'\,
'112291'\,
'112305'\,
'112313'\,
'112321'\,
'112330'\,
'112348'\,
'112356'\,
'112372'\,
'112381'\,
'112399'\,
'112402'\,
'112411'\,
'112429'\,
'112437'\,
'112453'\,
'112461'\,
'113018'\,
'113247'\,
'113263'\,
'113271'\,
'113417'\,
'113425'\,
'113433'\,
'113468'\,
'113476'\,
'113484'\,
'113492'\,
'113611'\,
'113620'\,
'113638'\,
'113654'\,
'113697'\,
'113816'\,
'113832'\,
'113859'\,
'114081'\,
'114421'\,
'114642'\,
'114651'\,
'120006'\,
'121002'\,
'122025'\,
'122033'\,
'122041'\,
'122050'\,
'122068'\,
'122076'\,
'122084'\,
'122106'\,
'122114'\,
'122122'\,
'122131'\,
'122157'\,
'122165'\,
'122173'\,
'122181'\,
'122190'\,
'122203'\,
'122211'\,
'122220'\,
'122238'\,
'122246'\,
'122254'\,
'122262'\,
'122271'\,
'122289'\,
'122297'\,
'122301'\,
'122319'\,
'122327'\,
'122335'\,
'122343'\,
'122351'\,
'122360'\,
'122378'\,
'122386'\,
'122394'\,
'123226'\,
'123293'\,
'123421'\,
'123471'\,
'123498'\,
'124036'\,
'124095'\,
'124109'\,
'124214'\,
'124222'\,
'124231'\,
'124249'\,
'124265'\,
'124273'\,
'124419'\,
'124435'\,
'124630'\,
'130001'\,
'131016'\,
'131024'\,
'131032'\,
'131041'\,
'131059'\,
'131067'\,
'131075'\,
'131083'\,
'131091'\,
'131105'\,
'131113'\,
'131121'\,
'131130'\,
'131148'\,
'131156'\,
'131164'\,
'131172'\,
'131181'\,
'131199'\,
'131202'\,
'131211'\,
'131229'\,
'131237'\,
'132012'\,
'132021'\,
'132039'\,
'132047'\,
'132055'\,
'132063'\,
'132071'\,
'132080'\,
'132098'\,
'132101'\,
'132110'\,
'132128'\,
'132136'\,
'132144'\,
'132152'\,
'132187'\,
'132195'\,
'132209'\,
'132217'\,
'132225'\,
'132233'\,
'132241'\,
'132250'\,
'132276'\,
'132284'\,
'132292'\,
'133035'\,
'133051'\,
'133078'\,
'133086'\,
'133612'\,
'133621'\,
'133639'\,
'133647'\,
'133817'\,
'133825'\,
'134015'\,
'134023'\,
'134210'\,
'140007'\,
'141003'\,
'141305'\,
'141500'\,
'142018'\,
'142034'\,
'142042'\,
'142051'\,
'142069'\,
'142077'\,
'142085'\,
'142107'\,
'142115'\,
'142123'\,
'142131'\,
'142140'\,
'142158'\,
'142166'\,
'142174'\,
'142182'\,
'143014'\,
'143219'\,
'143413'\,
'143421'\,
'143618'\,
'143626'\,
'143634'\,
'143642'\,
'143669'\,
'143821'\,
'143839'\,
'143847'\,
'144011'\,
'144029'\,
'150002'\,
'151009'\,
'152021'\,
'152048'\,
'152056'\,
'152064'\,
'152081'\,
'152099'\,
'152102'\,
'152111'\,
'152129'\,
'152137'\,
'152161'\,
'152170'\,
'152188'\,
'152226'\,
'152234'\,
'152242'\,
'152251'\,
'152269'\,
'152277'\,
'153079'\,
'153427'\,
'153613'\,
'153851'\,
'154059'\,
'154610'\,
'154822'\,
'155047'\,
'155811'\,
'155861'\,
'160008'\,
'162019'\,
'162027'\,
'162043'\,
'162051'\,
'162060'\,
'162078'\,
'162086'\,
'162094'\,
'162108'\,
'162116'\,
'163210'\,
'163228'\,
'163236'\,
'163422'\,
'163431'\,
'170003'\,
'172014'\,
'172022'\,
'172031'\,
'172049'\,
'172057'\,
'172065'\,
'172073'\,
'172090'\,
'172103'\,
'172111'\,
'172120'\,
'173240'\,
'173614'\,
'173657'\,
'173843'\,
'173860'\,
'174076'\,
'174611'\,
'174637'\,
'180009'\,
'182010'\,
'182028'\,
'182044'\,
'182052'\,
'182061'\,
'182079'\,
'182087'\,
'182095'\,
'182109'\,
'183229'\,
'183822'\,
'184047'\,
'184233'\,
'184420'\,
'184811'\,
'184837'\,
'185019'\,
'190004'\,
'192015'\,
'192023'\,
'192040'\,
'192058'\,
'192066'\,
'192074'\,
'192082'\,
'192091'\,
'192104'\,
'192112'\,
'192121'\,
'192139'\,
'192147'\,
'193461'\,
'193640'\,
'193658'\,
'193666'\,
'193682'\,
'193844'\,
'194221'\,
'194239'\,
'194247'\,
'194255'\,
'194298'\,
'194301'\,
'194425'\,
'194433'\,
'200000'\,
'202011'\,
'202029'\,
'202037'\,
'202045'\,
'202053'\,
'202061'\,
'202070'\,
'202088'\,
'202096'\,
'202100'\,
'202118'\,
'202126'\,
'202134'\,
'202142'\,
'202151'\,
'202177'\,
'202185'\,
'202193'\,
'202207'\,
'203033'\,
'203041'\,
'203050'\,
'203068'\,
'203076'\,
'203092'\,
'203211'\,
'203238'\,
'203246'\,
'203491'\,
'203505'\,
'203611'\,
'203629'\,
'203637'\,
'203823'\,
'203831'\,
'203840'\,
'203858'\,
'203866'\,
'203882'\,
'204021'\,
'204030'\,
'204048'\,
'204072'\,
'204099'\,
'204102'\,
'204111'\,
'204129'\,
'204137'\,
'204145'\,
'204153'\,
'204161'\,
'204170'\,
'204226'\,
'204234'\,
'204251'\,
'204293'\,
'204307'\,
'204323'\,
'204463'\,
'204480'\,
'204501'\,
'204510'\,
'204528'\,
'204811'\,
'204820'\,
'204854'\,
'204862'\,
'205214'\,
'205419'\,
'205435'\,
'205613'\,
'205621'\,
'205630'\,
'205834'\,
'205885'\,
'205907'\,
'206024'\,
'210005'\,
'212016'\,
'212024'\,
'212032'\,
'212041'\,
'212059'\,
'212067'\,
'212075'\,
'212083'\,
'212091'\,
'212105'\,
'212113'\,
'212121'\,
'212130'\,
'212148'\,
'212156'\,
'212164'\,
'212172'\,
'212181'\,
'212199'\,
'212202'\,
'212211'\,
'213021'\,
'213039'\,
'213411'\,
'213616'\,
'213624'\,
'213811'\,
'213829'\,
'213837'\,
'214019'\,
'214035'\,
'214043'\,
'214213'\,
'215015'\,
'215023'\,
'215031'\,
'215040'\,
'215058'\,
'215066'\,
'215074'\,
'215210'\,
'216046'\,
'220001'\,
'221007'\,
'221309'\,
'222038'\,
'222054'\,
'222062'\,
'222071'\,
'222089'\,
'222097'\,
'222101'\,
'222119'\,
'222127'\,
'222135'\,
'222143'\,
'222151'\,
'222160'\,
'222194'\,
'222208'\,
'222216'\,
'222224'\,
'222232'\,
'222241'\,
'222259'\,
'222267'\,
'223018'\,
'223026'\,
'223042'\,
'223051'\,
'223069'\,
'223255'\,
'223417'\,
'223425'\,
'223441'\,
'224243'\,
'224294'\,
'224618'\,
'230006'\,
'231002'\,
'232017'\,
'232025'\,
'232033'\,
'232041'\,
'232050'\,
'232068'\,
'232076'\,
'232084'\,
'232092'\,
'232106'\,
'232114'\,
'232122'\,
'232131'\,
'232149'\,
'232157'\,
'232165'\,
'232173'\,
'232190'\,
'232203'\,
'232211'\,
'232220'\,
'232238'\,
'232246'\,
'232254'\,
'232262'\,
'232271'\,
'232289'\,
'232297'\,
'232301'\,
'232319'\,
'232327'\,
'232335'\,
'232343'\,
'232351'\,
'232360'\,
'232378'\,
'232386'\,
'233021'\,
'233421'\,
'233617'\,
'233625'\,
'234249'\,
'234257'\,
'234273'\,
'234419'\,
'234427'\,
'234451'\,
'234460'\,
'234478'\,
'235016'\,
'235610'\,
'235628'\,
'235636'\,
'240001'\,
'242012'\,
'242021'\,
'242039'\,
'242047'\,
'242055'\,
'242071'\,
'242080'\,
'242098'\,
'242101'\,
'242110'\,
'242128'\,
'242144'\,
'242152'\,
'242161'\,
'243035'\,
'243248'\,
'243418'\,
'243434'\,
'243442'\,
'244414'\,
'244422'\,
'244431'\,
'244619'\,
'244708'\,
'244716'\,
'244724'\,
'245437'\,
'245615'\,
'245623'\,
'250007'\,
'252018'\,
'252026'\,
'252034'\,
'252042'\,
'252069'\,
'252077'\,
'252085'\,
'252093'\,
'252107'\,
'252115'\,
'252123'\,
'252131'\,
'252140'\,
'253839'\,
'253847'\,
'254258'\,
'254410'\,
'254428'\,
'254436'\,
'260002'\,
'261009'\,
'262013'\,
'262021'\,
'262030'\,
'262048'\,
'262056'\,
'262064'\,
'262072'\,
'262081'\,
'262099'\,
'262102'\,
'262111'\,
'262129'\,
'262137'\,
'262145'\,
'263036'\,
'263222'\,
'263435'\,
'263443'\,
'263648'\,
'263656'\,
'263664'\,
'263672'\,
'264075'\,
'264636'\,
'264652'\,
'270008'\,
'271004'\,
'271403'\,
'272027'\,
'272035'\,
'272043'\,
'272051'\,
'272060'\,
'272078'\,
'272086'\,
'272094'\,
'272108'\,
'272116'\,
'272124'\,
'272132'\,
'272141'\,
'272159'\,
'272167'\,
'272175'\,
'272183'\,
'272191'\,
'272205'\,
'272213'\,
'272221'\,
'272230'\,
'272248'\,
'272256'\,
'272264'\,
'272272'\,
'272281'\,
'272299'\,
'272302'\,
'272311'\,
'272329'\,
'273015'\,
'273210'\,
'273228'\,
'273414'\,
'273619'\,
'273627'\,
'273660'\,
'273813'\,
'273821'\,
'273830'\,
'280003'\,
'281000'\,
'282014'\,
'282022'\,
'282031'\,
'282049'\,
'282057'\,
'282065'\,
'282073'\,
'282081'\,
'282090'\,
'282103'\,
'282120'\,
'282138'\,
'282146'\,
'282154'\,
'282162'\,
'282171'\,
'282189'\,
'282197'\,
'282201'\,
'282219'\,
'282227'\,
'282235'\,
'282243'\,
'282251'\,
'282260'\,
'282278'\,
'282286'\,
'282294'\,
'283011'\,
'283657'\,
'283819'\,
'283827'\,
'284424'\,
'284432'\,
'284467'\,
'284645'\,
'284815'\,
'285013'\,
'285854'\,
'285862'\,
'290009'\,
'292010'\,
'292028'\,
'292036'\,
'292044'\,
'292052'\,
'292061'\,
'292079'\,
'292087'\,
'292095'\,
'292109'\,
'292117'\,
'292125'\,
'293229'\,
'293423'\,
'293431'\,
'293440'\,
'293458'\,
'293610'\,
'293628'\,
'293636'\,
'293857'\,
'293865'\,
'294012'\,
'294021'\,
'294241'\,
'294250'\,
'294268'\,
'294276'\,
'294411'\,
'294420'\,
'294438'\,
'294446'\,
'294462'\,
'294471'\,
'294497'\,
'294501'\,
'294519'\,
'294527'\,
'294535'\,
'300004'\,
'302015'\,
'302023'\,
'302031'\,
'302040'\,
'302058'\,
'302066'\,
'302074'\,
'302082'\,
'302091'\,
'303046'\,
'303411'\,
'303437'\,
'303445'\,
'303615'\,
'303623'\,
'303666'\,
'303810'\,
'303828'\,
'303836'\,
'303909'\,
'303917'\,
'303925'\,
'304018'\,
'304042'\,
'304069'\,
'304212'\,
'304221'\,
'304247'\,
'304271'\,
'304280'\,
'310000'\,
'312011'\,
'312029'\,
'312037'\,
'312045'\,
'313025'\,
'313254'\,
'313289'\,
'313297'\,
'313645'\,
'313700'\,
'313718'\,
'313726'\,
'313840'\,
'313866'\,
'313891'\,
'313904'\,
'314013'\,
'314021'\,
'314030'\,
'320005'\,
'322016'\,
'322024'\,
'322032'\,
'322041'\,
'322059'\,
'322067'\,
'322075'\,
'322091'\,
'323438'\,
'323861'\,
'324418'\,
'324485'\,
'324493'\,
'325015'\,
'325058'\,
'325252'\,
'325261'\,
'325279'\,
'325287'\,
'330001'\,
'331007'\,
'332020'\,
'332038'\,
'332046'\,
'332054'\,
'332071'\,
'332089'\,
'332097'\,
'332101'\,
'332119'\,
'332127'\,
'332135'\,
'332143'\,
'332151'\,
'332160'\,
'333468'\,
'334235'\,
'334456'\,
'334618'\,
'335860'\,
'336068'\,
'336220'\,
'336238'\,
'336432'\,
'336637'\,
'336661'\,
'336815'\,
'340006'\,
'341002'\,
'342025'\,
'342033'\,
'342041'\,
'342050'\,
'342076'\,
'342084'\,
'342092'\,
'342106'\,
'342114'\,
'342122'\,
'342131'\,
'342149'\,
'342157'\,
'343021'\,
'343048'\,
'343072'\,
'343099'\,
'343684'\,
'343692'\,
'344311'\,
'344621'\,
'345458'\,
'350001'\,
'352012'\,
'352021'\,
'352039'\,
'352047'\,
'352063'\,
'352071'\,
'352080'\,
'352101'\,
'352110'\,
'352128'\,
'352136'\,
'352152'\,
'352161'\,
'353051'\,
'353213'\,
'353418'\,
'353434'\,
'353442'\,
'355020'\,
'360007'\,
'362018'\,
'362026'\,
'362034'\,
'362042'\,
'362051'\,
'362069'\,
'362077'\,
'362085'\,
'363014'\,
'363022'\,
'363219'\,
'363413'\,
'363421'\,
'363685'\,
'363839'\,
'363871'\,
'363880'\,
'364011'\,
'364029'\,
'364037'\,
'364045'\,
'364053'\,
'364681'\,
'364894'\,
'370002'\,
'372013'\,
'372021'\,
'372030'\,
'372048'\,
'372056'\,
'372064'\,
'372072'\,
'372081'\,
'373222'\,
'373249'\,
'373419'\,
'373648'\,
'373869'\,
'373877'\,
'374032'\,
'374041'\,
'374067'\,
'380008'\,
'382019'\,
'382027'\,
'382035'\,
'382043'\,
'382051'\,
'382060'\,
'382078'\,
'382108'\,
'382132'\,
'382141'\,
'382159'\,
'383562'\,
'383864'\,
'384011'\,
'384020'\,
'384224'\,
'384429'\,
'384844'\,
'384887'\,
'385069'\,
'390003'\,
'392014'\,
'392022'\,
'392031'\,
'392049'\,
'392057'\,
'392065'\,
'392081'\,
'392090'\,
'392103'\,
'392111'\,
'392120'\,
'393011'\,
'393029'\,
'393037'\,
'393045'\,
'393053'\,
'393061'\,
'393070'\,
'393410'\,
'393444'\,
'393631'\,
'393649'\,
'393860'\,
'393878'\,
'394017'\,
'394025'\,
'394033'\,
'394050'\,
'394106'\,
'394114'\,
'394122'\,
'394246'\,
'394271'\,
'394289'\,
'400009'\,
'401005'\,
'401307'\,
'402028'\,
'402036'\,
'402044'\,
'402052'\,
'402061'\,
'402079'\,
'402109'\,
'402117'\,
'402125'\,
'402133'\,
'402141'\,
'402150'\,
'402168'\,
'402176'\,
'402184'\,
'402192'\,
'402206'\,
'402214'\,
'402231'\,
'402249'\,
'402257'\,
'402265'\,
'402273'\,
'402281'\,
'402290'\,
'402303'\,
'402311'\,
'403415'\,
'403423'\,
'403431'\,
'403440'\,
'403458'\,
'403482'\,
'403491'\,
'403814'\,
'403822'\,
'403831'\,
'403849'\,
'404012'\,
'404021'\,
'404217'\,
'404471'\,
'404489'\,
'405035'\,
'405221'\,
'405442'\,
'406015'\,
'406023'\,
'406040'\,
'406058'\,
'406082'\,
'406091'\,
'406104'\,
'406210'\,
'406252'\,
'406422'\,
'406465'\,
'406473'\,
'410004'\,
'412015'\,
'412023'\,
'412031'\,
'412040'\,
'412058'\,
'412066'\,
'412074'\,
'412082'\,
'412091'\,
'412104'\,
'413275'\,
'413411'\,
'413453'\,
'413461'\,
'413879'\,
'414018'\,
'414239'\,
'414247'\,
'414255'\,
'414417'\,
'420000'\,
'422011'\,
'422029'\,
'422037'\,
'422045'\,
'422053'\,
'422070'\,
'422088'\,
'422096'\,
'422100'\,
'422118'\,
'422126'\,
'422134'\,
'422142'\,
'423076'\,
'423084'\,
'423211'\,
'423220'\,
'423238'\,
'423831'\,
'423912'\,
'424111'\,
'430005'\,
'431001'\,
'432024'\,
'432032'\,
'432041'\,
'432059'\,
'432067'\,
'432083'\,
'432105'\,
'432113'\,
'432121'\,
'432130'\,
'432148'\,
'432156'\,
'432164'\,
'433489'\,
'433641'\,
'433675'\,
'433683'\,
'433691'\,
'434035'\,
'434043'\,
'434230'\,
'434248'\,
'434256'\,
'434281'\,
'434329'\,
'434337'\,
'434418'\,
'434426'\,
'434434'\,
'434442'\,
'434477'\,
'434680'\,
'434825'\,
'434841'\,
'435015'\,
'435058'\,
'435066'\,
'435074'\,
'435104'\,
'435112'\,
'435121'\,
'435139'\,
'435147'\,
'435317'\,
'440001'\,
'442011'\,
'442020'\,
'442038'\,
'442046'\,
'442054'\,
'442062'\,
'442071'\,
'442089'\,
'442097'\,
'442101'\,
'442119'\,
'442127'\,
'442135'\,
'442143'\,
'443221'\,
'443417'\,
'444618'\,
'444626'\,
'450006'\,
'452017'\,
'452025'\,
'452033'\,
'452041'\,
'452050'\,
'452068'\,
'452076'\,
'452084'\,
'452092'\,
'453412'\,
'453617'\,
'453820'\,
'453838'\,
'454010'\,
'454028'\,
'454036'\,
'454044'\,
'454052'\,
'454061'\,
'454214'\,
'454290'\,
'454303'\,
'454311'\,
'454419'\,
'454427'\,
'454435'\,
'460001'\,
'462012'\,
'462039'\,
'462047'\,
'462063'\,
'462080'\,
'462101'\,
'462136'\,
'462144'\,
'462152'\,
'462161'\,
'462179'\,
'462187'\,
'462195'\,
'462209'\,
'462217'\,
'462225'\,
'462233'\,
'462241'\,
'462250'\,
'463035'\,
'463043'\,
'463922'\,
'464040'\,
'464520'\,
'464686'\,
'464821'\,
'464902'\,
'464911'\,
'464929'\,
'465011'\,
'465020'\,
'465054'\,
'465232'\,
'465241'\,
'465259'\,
'465275'\,
'465291'\,
'465305'\,
'465313'\,
'465321'\,
'465330'\,
'465348'\,
'465356'\,
'470007'\,
'472018'\,
'472051'\,
'472077'\,
'472085'\,
'472093'\,
'472107'\,
'472115'\,
'472123'\,
'472131'\,
'472140'\,
'472158'\,
'473014'\,
'473022'\,
'473031'\,
'473065'\,
'473081'\,
'473111'\,
'473138'\,
'473146'\,
'473154'\,
'473243'\,
'473251'\,
'473260'\,
'473278'\,
'473286'\,
'473294'\,
'473481'\,
'473502'\,
'473537'\,
'473545'\,
'473553'\,
'473561'\,
'473570'\,
'473588'\,
'473596'\,
'473600'\,
'473618'\,
'473626'\,
'473758'\,
'473812'\,
'473821']

 #  return ['Traditional-Festivalsand-annual-events' , 'Shrine-floats-etc.'  , 'Traditional-performing-arts-and-dance' ,'Procession-and-parade' ,'food'   ,'market'   , \
  #             'flower-nature'   ,'fire'   ,'fireworks'   ,'snow'  ,'illumination'  ,'music'  ,'sports'  ,'museum'  ,'museum'  ,'festival'  ,'animal'  ,'experience'  ,  \
  #          'school'  ,'talk'  ,'stage'  ,'thema-park'  ,'animal-fish-park'  ,'anniversary'  ,'fair'  ,'other'  ,'Industry'  ,'Customs'  ,'Agricultural ritual' ,'none']

   # return ['Traditional-Festivalsand-annual-events','Shrine-floats-etc.' , 'dokujo-tsushin', 'it-life-hack', 'kaden-channel', 'livedoor-homme', 'movie-enter', 'peachy', 'smax', 'sports-watch', 'topic-news']

  def _create_examples(self, lines, set_type):
    """Creates examples for the training and dev sets."""
    examples = []
    for (i, line) in enumerate(lines):
      if i == 0:
        idx_text = line.index('text')
        idx_label = line.index('label')
      else:
        guid = "%s-%s" % (set_type, i)
        text_a = tokenization.convert_to_unicode(line[idx_text])
        label = tokenization.convert_to_unicode(line[idx_label])
        examples.append(
            InputExample(guid=guid, text_a=text_a, text_b=None, label=label))
    return examples


def convert_single_example(ex_index, example, label_list, max_seq_length,
                           tokenizer):
  """Converts a single `InputExample` into a single `InputFeatures`."""

  if isinstance(example, PaddingInputExample):
    return InputFeatures(
        input_ids=[0] * max_seq_length,
        input_mask=[0] * max_seq_length,
        segment_ids=[0] * max_seq_length,
        label_id=0,
        is_real_example=False)

  label_map = {}
  for (i, label) in enumerate(label_list):
    label_map[label] = i

  tokens_a = tokenizer.tokenize(example.text_a)
  tokens_b = None
  if example.text_b:
    tokens_b = tokenizer.tokenize(example.text_b)

  if tokens_b:
    # Modifies `tokens_a` and `tokens_b` in place so that the total
    # length is less than the specified length.
    # Account for [CLS], [SEP], [SEP] with "- 3"
    _truncate_seq_pair(tokens_a, tokens_b, max_seq_length - 3)
  else:
    # Account for [CLS] and [SEP] with "- 2"
    if len(tokens_a) > max_seq_length - 2:
      tokens_a = tokens_a[0:(max_seq_length - 2)]

  # The convention in BERT is:
  # (a) For sequence pairs:
  #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
  #  type_ids: 0     0  0    0    0     0       0 0     1  1  1  1   1 1
  # (b) For single sequences:
  #  tokens:   [CLS] the dog is hairy . [SEP]
  #  type_ids: 0     0   0   0  0     0 0
  #
  # Where "type_ids" are used to indicate whether this is the first
  # sequence or the second sequence. The embedding vectors for `type=0` and
  # `type=1` were learned during pre-training and are added to the wordpiece
  # embedding vector (and position vector). This is not *strictly* necessary
  # since the [SEP] token unambiguously separates the sequences, but it makes
  # it easier for the model to learn the concept of sequences.
  #
  # For classification tasks, the first vector (corresponding to [CLS]) is
  # used as the "sentence vector". Note that this only makes sense because
  # the entire model is fine-tuned.
  tokens = []
  segment_ids = []
  tokens.append("[CLS]")
  segment_ids.append(0)
  for token in tokens_a:
    tokens.append(token)
    segment_ids.append(0)
  tokens.append("[SEP]")
  segment_ids.append(0)

  if tokens_b:
    for token in tokens_b:
      tokens.append(token)
      segment_ids.append(1)
    tokens.append("[SEP]")
    segment_ids.append(1)

  input_ids = tokenizer.convert_tokens_to_ids(tokens)

  # The mask has 1 for real tokens and 0 for padding tokens. Only real
  # tokens are attended to.
  input_mask = [1] * len(input_ids)

  # Zero-pad up to the sequence length.
  while len(input_ids) < max_seq_length:
    input_ids.append(0)
    input_mask.append(0)
    segment_ids.append(0)

  assert len(input_ids) == max_seq_length
  assert len(input_mask) == max_seq_length
  assert len(segment_ids) == max_seq_length

  label_id = label_map[example.label]
  if ex_index < 5:
    tf.logging.info("*** Example ***")
    tf.logging.info("guid: %s" % (example.guid))
    tf.logging.info("tokens: %s" % " ".join(
        [tokenization.printable_text(x) for x in tokens]))
    tf.logging.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
    tf.logging.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
    tf.logging.info("segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
    tf.logging.info("label: %s (id = %d)" % (example.label, label_id))

  feature = InputFeatures(
      input_ids=input_ids,
      input_mask=input_mask,
      segment_ids=segment_ids,
      label_id=label_id,
      is_real_example=True)
  return feature


def file_based_convert_examples_to_features(
    examples, label_list, max_seq_length, tokenizer, output_file):
  """Convert a set of `InputExample`s to a TFRecord file."""

  writer = tf.python_io.TFRecordWriter(output_file)

  for (ex_index, example) in enumerate(examples):
    if ex_index % 10000 == 0:
      tf.logging.info("Writing example %d of %d" % (ex_index, len(examples)))

    feature = convert_single_example(ex_index, example, label_list,
                                     max_seq_length, tokenizer)

    def create_int_feature(values):
      f = tf.train.Feature(int64_list=tf.train.Int64List(value=list(values)))
      return f

    features = collections.OrderedDict()
    features["input_ids"] = create_int_feature(feature.input_ids)
    features["input_mask"] = create_int_feature(feature.input_mask)
    features["segment_ids"] = create_int_feature(feature.segment_ids)
    features["label_ids"] = create_int_feature([feature.label_id])
    features["is_real_example"] = create_int_feature(
        [int(feature.is_real_example)])

    tf_example = tf.train.Example(features=tf.train.Features(feature=features))
    writer.write(tf_example.SerializeToString())
  writer.close()


def file_based_input_fn_builder(input_file, seq_length, is_training,
                                drop_remainder):
  """Creates an `input_fn` closure to be passed to TPUEstimator."""

  name_to_features = {
      "input_ids": tf.FixedLenFeature([seq_length], tf.int64),
      "input_mask": tf.FixedLenFeature([seq_length], tf.int64),
      "segment_ids": tf.FixedLenFeature([seq_length], tf.int64),
      "label_ids": tf.FixedLenFeature([], tf.int64),
      "is_real_example": tf.FixedLenFeature([], tf.int64),
  }

  def _decode_record(record, name_to_features):
    """Decodes a record to a TensorFlow example."""
    example = tf.parse_single_example(record, name_to_features)

    # tf.Example only supports tf.int64, but the TPU only supports tf.int32.
    # So cast all int64 to int32.
    for name in list(example.keys()):
      t = example[name]
      if t.dtype == tf.int64:
        t = tf.to_int32(t)
      example[name] = t

    return example

  def input_fn(params):
    """The actual input function."""
    batch_size = params["batch_size"]

    # For training, we want a lot of parallel reading and shuffling.
    # For eval, we want no shuffling and parallel reading doesn't matter.
    d = tf.data.TFRecordDataset(input_file)
    if is_training:
      d = d.repeat()
      d = d.shuffle(buffer_size=100)

    d = d.apply(
        tf.contrib.data.map_and_batch(
            lambda record: _decode_record(record, name_to_features),
            batch_size=batch_size,
            drop_remainder=drop_remainder))

    return d

  return input_fn


def _truncate_seq_pair(tokens_a, tokens_b, max_length):
  """Truncates a sequence pair in place to the maximum length."""

  # This is a simple heuristic which will always truncate the longer sequence
  # one token at a time. This makes more sense than truncating an equal percent
  # of tokens from each, since if one sequence is very short then each token
  # that's truncated likely contains more information than a longer sequence.
  while True:
    total_length = len(tokens_a) + len(tokens_b)
    if total_length <= max_length:
      break
    if len(tokens_a) > len(tokens_b):
      tokens_a.pop()
    else:
      tokens_b.pop()


def create_model(bert_config, is_training, input_ids, input_mask, segment_ids,
                 labels, num_labels, use_one_hot_embeddings):
  """Creates a classification model."""
  model = modeling.BertModel(
      config=bert_config,
      is_training=is_training,
      input_ids=input_ids,
      input_mask=input_mask,
      token_type_ids=segment_ids,
      use_one_hot_embeddings=use_one_hot_embeddings)

  # In the demo, we are doing a simple classification task on the entire
  # segment.
  #
  # If you want to use the token-level output, use model.get_sequence_output()
  # instead.
  output_layer = model.get_pooled_output()

  hidden_size = output_layer.shape[-1].value

  output_weights = tf.get_variable(
      "output_weights", [num_labels, hidden_size],
      initializer=tf.truncated_normal_initializer(stddev=0.02))

  output_bias = tf.get_variable(
      "output_bias", [num_labels], initializer=tf.zeros_initializer())

  with tf.variable_scope("loss"):
    if is_training:
      # I.e., 0.1 dropout
      output_layer = tf.nn.dropout(output_layer, keep_prob=0.9)

    logits = tf.matmul(output_layer, output_weights, transpose_b=True)
    logits = tf.nn.bias_add(logits, output_bias)
    probabilities = tf.nn.softmax(logits, axis=-1)
    log_probs = tf.nn.log_softmax(logits, axis=-1)

    one_hot_labels = tf.one_hot(labels, depth=num_labels, dtype=tf.float32)

    per_example_loss = -tf.reduce_sum(one_hot_labels * log_probs, axis=-1)
    loss = tf.reduce_mean(per_example_loss)

    return (loss, per_example_loss, logits, probabilities)


def model_fn_builder(bert_config, num_labels, init_checkpoint, learning_rate,
                     num_train_steps, num_warmup_steps, use_tpu,
                     use_one_hot_embeddings):
  """Returns `model_fn` closure for TPUEstimator."""

  def model_fn(features, labels, mode, params):  # pylint: disable=unused-argument
    """The `model_fn` for TPUEstimator."""

    tf.logging.info("*** Features ***")
    for name in sorted(features.keys()):
      tf.logging.info("  name = %s, shape = %s" % (name, features[name].shape))

    input_ids = features["input_ids"]
    input_mask = features["input_mask"]
    segment_ids = features["segment_ids"]
    label_ids = features["label_ids"]
    is_real_example = None
    if "is_real_example" in features:
      is_real_example = tf.cast(features["is_real_example"], dtype=tf.float32)
    else:
      is_real_example = tf.ones(tf.shape(label_ids), dtype=tf.float32)

    is_training = (mode == tf.estimator.ModeKeys.TRAIN)

    (total_loss, per_example_loss, logits, probabilities) = create_model(
        bert_config, is_training, input_ids, input_mask, segment_ids, label_ids,
        num_labels, use_one_hot_embeddings)

    tvars = tf.trainable_variables()
    initialized_variable_names = {}
    scaffold_fn = None
    if init_checkpoint:
      (assignment_map, initialized_variable_names
       ) = modeling.get_assignment_map_from_checkpoint(tvars, init_checkpoint)
      if use_tpu:

        def tpu_scaffold():
          tf.train.init_from_checkpoint(init_checkpoint, assignment_map)
          return tf.train.Scaffold()

        scaffold_fn = tpu_scaffold
      else:
        tf.train.init_from_checkpoint(init_checkpoint, assignment_map)

    tf.logging.info("**** Trainable Variables ****")
    for var in tvars:
      init_string = ""
      if var.name in initialized_variable_names:
        init_string = ", *INIT_FROM_CKPT*"
      tf.logging.info("  name = %s, shape = %s%s", var.name, var.shape,
                      init_string)

    output_spec = None
    if mode == tf.estimator.ModeKeys.TRAIN:

      train_op = optimization.create_optimizer(
          total_loss, learning_rate, num_train_steps, num_warmup_steps, use_tpu)

      output_spec = tf.contrib.tpu.TPUEstimatorSpec(
          mode=mode,
          loss=total_loss,
          train_op=train_op,
          scaffold_fn=scaffold_fn)
    elif mode == tf.estimator.ModeKeys.EVAL:

      def metric_fn(per_example_loss, label_ids, logits, is_real_example):
        predictions = tf.argmax(logits, axis=-1, output_type=tf.int32)
        accuracy = tf.metrics.accuracy(
            labels=label_ids, predictions=predictions, weights=is_real_example)
        loss = tf.metrics.mean(values=per_example_loss, weights=is_real_example)
        return {
            "eval_accuracy": accuracy,
            "eval_loss": loss,
        }

      eval_metrics = (metric_fn,
                      [per_example_loss, label_ids, logits, is_real_example])
      output_spec = tf.contrib.tpu.TPUEstimatorSpec(
          mode=mode,
          loss=total_loss,
          eval_metrics=eval_metrics,
          scaffold_fn=scaffold_fn)
    else:
      output_spec = tf.contrib.tpu.TPUEstimatorSpec(
          mode=mode,
          predictions={"probabilities": probabilities},
          scaffold_fn=scaffold_fn)
    return output_spec

  return model_fn


# This function is not used by this file but is still used by the Colab and
# people who depend on it.
def input_fn_builder(features, seq_length, is_training, drop_remainder):
  """Creates an `input_fn` closure to be passed to TPUEstimator."""

  all_input_ids = []
  all_input_mask = []
  all_segment_ids = []
  all_label_ids = []

  for feature in features:
    all_input_ids.append(feature.input_ids)
    all_input_mask.append(feature.input_mask)
    all_segment_ids.append(feature.segment_ids)
    all_label_ids.append(feature.label_id)

  def input_fn(params):
    """The actual input function."""
    batch_size = params["batch_size"]

    num_examples = len(features)

    # This is for demo purposes and does NOT scale to large data sets. We do
    # not use Dataset.from_generator() because that uses tf.py_func which is
    # not TPU compatible. The right way to load data is with TFRecordReader.
    d = tf.data.Dataset.from_tensor_slices({
        "input_ids":
            tf.constant(
                all_input_ids, shape=[num_examples, seq_length],
                dtype=tf.int32),
        "input_mask":
            tf.constant(
                all_input_mask,
                shape=[num_examples, seq_length],
                dtype=tf.int32),
        "segment_ids":
            tf.constant(
                all_segment_ids,
                shape=[num_examples, seq_length],
                dtype=tf.int32),
        "label_ids":
            tf.constant(all_label_ids, shape=[num_examples], dtype=tf.int32),
    })

    if is_training:
      d = d.repeat()
      d = d.shuffle(buffer_size=100)

    d = d.batch(batch_size=batch_size, drop_remainder=drop_remainder)
    return d

  return input_fn


# This function is not used by this file but is still used by the Colab and
# people who depend on it.
def convert_examples_to_features(examples, label_list, max_seq_length,
                                 tokenizer):
  """Convert a set of `InputExample`s to a list of `InputFeatures`."""

  features = []
  for (ex_index, example) in enumerate(examples):
    if ex_index % 10000 == 0:
      tf.logging.info("Writing example %d of %d" % (ex_index, len(examples)))

    feature = convert_single_example(ex_index, example, label_list,
                                     max_seq_length, tokenizer)

    features.append(feature)
  return features


def main(_):
  tf.logging.set_verbosity(tf.logging.INFO)

  processors = {
      "livedoor": LivedoorProcessor,
  }

  tokenization.validate_case_matches_checkpoint(FLAGS.do_lower_case,
                                                FLAGS.init_checkpoint)

  if not FLAGS.do_train and not FLAGS.do_eval and not FLAGS.do_predict:
    raise ValueError(
        "At least one of `do_train`, `do_eval` or `do_predict' must be True.")

  bert_config = modeling.BertConfig.from_json_file(bert_config_file.name)

  if FLAGS.max_seq_length > bert_config.max_position_embeddings:
    raise ValueError(
        "Cannot use sequence length %d because the BERT model "
        "was only trained up to sequence length %d" %
        (FLAGS.max_seq_length, bert_config.max_position_embeddings))

  tf.gfile.MakeDirs(FLAGS.output_dir)

  task_name = FLAGS.task_name.lower()

  if task_name not in processors:
    raise ValueError("Task not found: %s" % (task_name))

  processor = processors[task_name]()

  label_list = processor.get_labels()

  tokenizer = tokenization.FullTokenizer(
      model_file=FLAGS.model_file, vocab_file=FLAGS.vocab_file,
      do_lower_case=FLAGS.do_lower_case)

  tpu_cluster_resolver = None
  if FLAGS.use_tpu and FLAGS.tpu_name:
    tpu_cluster_resolver = tf.contrib.cluster_resolver.TPUClusterResolver(
        FLAGS.tpu_name, zone=FLAGS.tpu_zone, project=FLAGS.gcp_project)

  is_per_host = tf.contrib.tpu.InputPipelineConfig.PER_HOST_V2
  run_config = tf.contrib.tpu.RunConfig(
      cluster=tpu_cluster_resolver,
      master=FLAGS.master,
      model_dir=FLAGS.output_dir,
      save_checkpoints_steps=FLAGS.save_checkpoints_steps,
      tpu_config=tf.contrib.tpu.TPUConfig(
          iterations_per_loop=FLAGS.iterations_per_loop,
          num_shards=FLAGS.num_tpu_cores,
          per_host_input_for_training=is_per_host))

  train_examples = None
  num_train_steps = None
  num_warmup_steps = None
  if FLAGS.do_train:
    train_examples = processor.get_train_examples(FLAGS.data_dir)
    num_train_steps = int(
        len(train_examples) / FLAGS.train_batch_size * FLAGS.num_train_epochs)
    num_warmup_steps = int(num_train_steps * FLAGS.warmup_proportion)

  model_fn = model_fn_builder(
      bert_config=bert_config,
      num_labels=len(label_list),
      init_checkpoint=FLAGS.init_checkpoint,
      learning_rate=FLAGS.learning_rate,
      num_train_steps=num_train_steps,
      num_warmup_steps=num_warmup_steps,
      use_tpu=FLAGS.use_tpu,
      use_one_hot_embeddings=FLAGS.use_tpu)

  # If TPU is not available, this will fall back to normal Estimator on CPU
  # or GPU.
  estimator = tf.contrib.tpu.TPUEstimator(
      use_tpu=FLAGS.use_tpu,
      model_fn=model_fn,
      config=run_config,
      train_batch_size=FLAGS.train_batch_size,
      eval_batch_size=FLAGS.eval_batch_size,
      predict_batch_size=FLAGS.predict_batch_size)

  if FLAGS.do_train:
    train_file = os.path.join(FLAGS.output_dir, "train.tf_record")
    file_based_convert_examples_to_features(
        train_examples, label_list, FLAGS.max_seq_length, tokenizer, train_file)
    tf.logging.info("***** Running training *****")
    tf.logging.info("  Num examples = %d", len(train_examples))
    tf.logging.info("  Batch size = %d", FLAGS.train_batch_size)
    tf.logging.info("  Num steps = %d", num_train_steps)
    train_input_fn = file_based_input_fn_builder(
        input_file=train_file,
        seq_length=FLAGS.max_seq_length,
        is_training=True,
        drop_remainder=True)
    estimator.train(input_fn=train_input_fn, max_steps=num_train_steps)

  if FLAGS.do_eval:
    eval_examples = processor.get_dev_examples(FLAGS.data_dir)
    num_actual_eval_examples = len(eval_examples)
    if FLAGS.use_tpu:
      # TPU requires a fixed batch size for all batches, therefore the number
      # of examples must be a multiple of the batch size, or else examples
      # will get dropped. So we pad with fake examples which are ignored
      # later on. These do NOT count towards the metric (all tf.metrics
      # support a per-instance weight, and these get a weight of 0.0).
      while len(eval_examples) % FLAGS.eval_batch_size != 0:
        eval_examples.append(PaddingInputExample())

    eval_file = os.path.join(FLAGS.output_dir, "eval.tf_record")
    file_based_convert_examples_to_features(
        eval_examples, label_list, FLAGS.max_seq_length, tokenizer, eval_file)

    tf.logging.info("***** Running evaluation *****")
    tf.logging.info("  Num examples = %d (%d actual, %d padding)",
                    len(eval_examples), num_actual_eval_examples,
                    len(eval_examples) - num_actual_eval_examples)
    tf.logging.info("  Batch size = %d", FLAGS.eval_batch_size)

    # This tells the estimator to run through the entire set.
    eval_steps = None
    # However, if running eval on the TPU, you will need to specify the
    # number of steps.
    if FLAGS.use_tpu:
      assert len(eval_examples) % FLAGS.eval_batch_size == 0
      eval_steps = int(len(eval_examples) // FLAGS.eval_batch_size)

    eval_drop_remainder = True if FLAGS.use_tpu else False
    eval_input_fn = file_based_input_fn_builder(
        input_file=eval_file,
        seq_length=FLAGS.max_seq_length,
        is_training=False,
        drop_remainder=eval_drop_remainder)

    result = estimator.evaluate(input_fn=eval_input_fn, steps=eval_steps)

    output_eval_file = os.path.join(FLAGS.output_dir, "eval_results.txt")
    with tf.gfile.GFile(output_eval_file, "w") as writer:
      tf.logging.info("***** Eval results *****")
      for key in sorted(result.keys()):
        tf.logging.info("  %s = %s", key, str(result[key]))
        writer.write("%s = %s\n" % (key, str(result[key])))

  if FLAGS.do_predict:
    predict_examples = processor.get_test_examples(FLAGS.data_dir)
    num_actual_predict_examples = len(predict_examples)
    if FLAGS.use_tpu:
      # TPU requires a fixed batch size for all batches, therefore the number
      # of examples must be a multiple of the batch size, or else examples
      # will get dropped. So we pad with fake examples which are ignored
      # later on.
      while len(predict_examples) % FLAGS.predict_batch_size != 0:
        predict_examples.append(PaddingInputExample())

    predict_file = os.path.join(FLAGS.output_dir, "predict.tf_record")
    file_based_convert_examples_to_features(predict_examples, label_list,
                                            FLAGS.max_seq_length, tokenizer,
                                            predict_file)

    tf.logging.info("***** Running prediction*****")
    tf.logging.info("  Num examples = %d (%d actual, %d padding)",
                    len(predict_examples), num_actual_predict_examples,
                    len(predict_examples) - num_actual_predict_examples)
    tf.logging.info("  Batch size = %d", FLAGS.predict_batch_size)

    predict_drop_remainder = True if FLAGS.use_tpu else False
    predict_input_fn = file_based_input_fn_builder(
        input_file=predict_file,
        seq_length=FLAGS.max_seq_length,
        is_training=False,
        drop_remainder=predict_drop_remainder)

    result = estimator.predict(input_fn=predict_input_fn)

    output_predict_file = os.path.join(FLAGS.output_dir, "test_results.tsv")
    with tf.gfile.GFile(output_predict_file, "w") as writer:
      num_written_lines = 0
      tf.logging.info("***** Predict results *****")
      for (i, prediction) in enumerate(result):
        probabilities = prediction["probabilities"]
        if i >= num_actual_predict_examples:
          break
        output_line = "\t".join(
            str(class_probability)
            for class_probability in probabilities) + "\n"
        writer.write(output_line)
        num_written_lines += 1
    assert num_written_lines == num_actual_predict_examples


if __name__ == "__main__":
  flags.mark_flag_as_required("data_dir")
  flags.mark_flag_as_required("task_name")
  flags.mark_flag_as_required("model_file")
  flags.mark_flag_as_required("vocab_file")
  flags.mark_flag_as_required("output_dir")
  tf.app.run()
