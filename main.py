# !pip install segmentation-models
import keras
import warnings
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from PIL import Image
from segmentation_models import Unet
from segmentation_models.backbones import get_preprocessing
from keras.models import load_model

batch_size = 16
img_resize_shape = (128, 800)
img_source_shape = (256, 1600)
in_channels = 3
out_channels = 4
path = '/kaggle/input/severstal-steel-defect-detection/'
epochs = 1


class DataGenerator(keras.utils.Sequence):
    def __init__(self, df, subset="train", shuffle=False, preprocess=None):
        super().__init__()
        self.df = df
        self.shuffle = shuffle
        self.subset = subset
        self.batch_size = batch_size
        self.preprocess = preprocess
        self.info = {}
        if self.subset == "train":
            self.data_path = path + 'train_images/'
        elif self.subset == "test":
            self.data_path = path + 'test_images/'
        self.on_epoch_end()

    def __len__(self):
        return int(np.floor(len(self.df) / self.batch_size))

    def on_epoch_end(self):
        self.indexes = np.arange(len(self.df))
        if self.shuffle:
            np.random.shuffle(self.indexes)

    def __getitem__(self, index):
        x = np.empty((self.batch_size, img_resize_shape[0], img_resize_shape[1], in_channels), dtype=np.float32)
        y = np.empty((self.batch_size, img_resize_shape[0], img_resize_shape[1], out_channels), dtype=np.int8)
        indexes = self.indexes[index * self.batch_size:(index + 1) * self.batch_size]
        for i, f in enumerate(self.df['ImageId'].iloc[indexes]):
            self.info[index * self.batch_size + i] = f
            x[i,] = Image.open(self.data_path + f).resize((img_resize_shape[1], img_resize_shape[0]))
            if self.subset == 'train':
                for j in range(4):
                    y[i, :, :, j] = rle2maskResize(self.df['e' + str(j + 1)].iloc[indexes[i]])
        if self.preprocess is not None:
            x = self.preprocess(x)
        return x, y if self.subset == 'train' else x


def rle2maskResize(rle):
    # CONVERT RLE TO MASK
    if (pd.isnull(rle)) | (rle == ''):
        return np.zeros(img_resize_shape, dtype=np.uint8)
    mask = np.zeros(img_source_shape[1] * img_source_shape[0], dtype=np.uint8)
    array = np.asarray([int(x) for x in rle.split()])
    starts = array[0::2] - 1
    lengths = array[1::2]
    for index, start in enumerate(starts):
        mask[int(start):int(start + lengths[index])] = 1
    return mask.reshape(img_source_shape, order='F')[::2, ::2]


def mask2contour(mask, width=3):
    # CONVERT MASK TO ITS CONTOUR
    w = mask.shape[1]
    h = mask.shape[0]
    mask2 = np.concatenate([mask[:, width:], np.zeros((h, width))], axis=1)
    mask2 = np.logical_xor(mask, mask2)
    mask3 = np.concatenate([mask[width:, :], np.zeros((width, w))], axis=0)
    mask3 = np.logical_xor(mask, mask3)
    return np.logical_or(mask2, mask3)


def mask2pad(mask, pad=2):
    # ENLARGE MASK TO INCLUDE MORE SPACE AROUND DEFECT
    w = mask.shape[1]
    h = mask.shape[0]

    # MASK UP
    for k in range(1, pad, 2):
        temp = np.concatenate([mask[k:, :], np.zeros((k, w))], axis=0)
        mask = np.logical_or(mask, temp)
    # MASK DOWN
    for k in range(1, pad, 2):
        temp = np.concatenate([np.zeros((k, w)), mask[:-k, :]], axis=0)
        mask = np.logical_or(mask, temp)
    # MASK LEFT
    for k in range(1, pad, 2):
        temp = np.concatenate([mask[:, k:], np.zeros((h, k))], axis=1)
        mask = np.logical_or(mask, temp)
    # MASK RIGHT
    for k in range(1, pad, 2):
        temp = np.concatenate([np.zeros((h, k)), mask[:, :-k]], axis=1)
        mask = np.logical_or(mask, temp)

    return mask


# COMPETITION METRIC
def dice_coef(y_true, y_pred, smooth=1):
    y_true_f = keras.backend.flatten(y_true)
    y_pred_f = keras.backend.flatten(y_pred)
    intersection = keras.backend.sum(y_true_f * y_pred_f)
    return (2. * intersection + smooth) / (keras.backend.sum(y_true_f) + keras.backend.sum(y_pred_f) + smooth)


def data_prep():
    global train2
    train = pd.read_csv(path + 'train.csv')
    # RESTRUCTURE TRAIN DATAFRAME
    train['ImageId'] = train['ImageId_ClassId'].map(lambda x: x.split('.')[0] + '.jpg')
    train2 = pd.DataFrame({'ImageId': train['ImageId'][::4]})
    train2['e1'] = train['EncodedPixels'][::4].values
    train2['e2'] = train['EncodedPixels'][1::4].values
    train2['e3'] = train['EncodedPixels'][2::4].values
    train2['e4'] = train['EncodedPixels'][3::4].values
    train2.reset_index(inplace=True, drop=True)
    train2.fillna('', inplace=True)
    train2['count'] = np.sum(train2.iloc[:, 1:] != '', axis=1).values
    train2.head(10)
    print(train.shape)
    print(train2.shape)


def data_inspection():
    global defects, train_batches, i, batch, k, img, extra, j, msk
    # DEFECTIVE IMAGE SAMPLES
    defects = list(train2[train2['e1'] != ''].sample(4).index)
    defects += list(train2[train2['e2'] != ''].sample(4).index)
    defects += list(train2[train2['e3'] != ''].sample(4).index)
    defects += list(train2[train2['e4'] != ''].sample(4).index)
    # DATA GENERATOR
    train_batches = DataGenerator(train2[train2.index.isin(defects)], shuffle=True)
    print('Images and masks from our Data Generator')
    print('KEY: yellow=defect1, green=defect2, blue=defect3, magenta=defect4')
    # DISPLAY IMAGES WITH DEFECTS
    for i, batch in enumerate(train_batches):
        plt.figure(figsize=(14, 50))  # 20,18
        for k in range(16):
            plt.subplot(16, 1, k + 1)
            img = batch[0][k,]
            img = Image.fromarray(img.astype('uint8'))
            img = np.array(img)
            extra = '  has defect'
            for j in range(4):
                msk = batch[1][k, :, :, j]
                msk = mask2pad(msk, pad=3)
                msk = mask2contour(msk, width=2)
                if np.sum(msk) != 0: extra += ' ' + str(j + 1)
                if j == 0:  # yellow
                    img[msk == 1, 0] = 235
                    img[msk == 1, 1] = 235
                elif j == 1:
                    img[msk == 1, 1] = 210  # green
                elif j == 2:
                    img[msk == 1, 2] = 255  # blue
                elif j == 3:  # magenta
                    img[msk == 1, 0] = 255
                    img[msk == 1, 2] = 255
            plt.title(train_batches.info[16 * i + k] + extra)
            plt.axis('off')
            plt.imshow(img)
        plt.subplots_adjust(wspace=0.05)
        plt.show()


def network_setup():
    global preprocess, model, idx, train_batches, valid_batches
    # LOAD UNET WITH PRETRAINING FROM IMAGENET
    preprocess = get_preprocessing('resnet34')  # for resnet, img = (img-110.0)/1.0
    model = Unet('resnet34', input_shape=(img_resize_shape[0], img_resize_shape[1], in_channels), classes=out_channels,
                 activation='sigmoid')
    model.compile(optimizer='adam', loss='binary_crossentropy', metrics=[dice_coef])
    # TRAIN AND VALIDATE MODEL
    idx = int(0.8 * len(train2))
    print()
    train_batches = DataGenerator(train2.iloc[:idx], shuffle=True, preprocess=preprocess)
    valid_batches = DataGenerator(train2.iloc[idx:], preprocess=preprocess)
    history = model.fit_generator(train_batches, validation_data=valid_batches, epochs=epochs, verbose=1)


def network_inspection():
    global defects, valid_batches, preds, i, batch, k, img, extra, j, msk
    # PREDICT FROM VALIDATION SET (ONLY IMAGES WITH DEFECTS)
    val_set = train2.iloc[idx:]
    defects = list(val_set[val_set['e1'] != ''].sample(6).index)
    defects += list(val_set[val_set['e2'] != ''].sample(6).index)
    defects += list(val_set[val_set['e3'] != ''].sample(14).index)
    defects += list(val_set[val_set['e4'] != ''].sample(6).index)
    valid_batches = DataGenerator(val_set[val_set.index.isin(defects)], preprocess=preprocess)
    preds = model.predict_generator(valid_batches, verbose=1)
    # PLOT PREDICTIONS
    valid_batches = DataGenerator(val_set[val_set.index.isin(defects)])
    print('Plotting predictions...')
    print('KEY: yellow=defect1, green=defect2, blue=defect3, magenta=defect4')
    for i, batch in enumerate(valid_batches):
        plt.figure(figsize=(20, 36))
        for k in range(16):
            plt.subplot(16, 2, 2 * k + 1)
            img = batch[0][k,]
            img = Image.fromarray(img.astype('uint8'))
            img = np.array(img)
            dft = 0
            extra = '  has defect '
            for j in range(4):
                msk = batch[1][k, :, :, j]
                if np.sum(msk) != 0:
                    dft = j + 1
                    extra += ' ' + str(j + 1)
                msk = mask2pad(msk, pad=2)
                msk = mask2contour(msk, width=3)
                if j == 0:  # yellow
                    img[msk == 1, 0] = 235
                    img[msk == 1, 1] = 235
                elif j == 1:
                    img[msk == 1, 1] = 210  # green
                elif j == 2:
                    img[msk == 1, 2] = 255  # blue
                elif j == 3:  # magenta
                    img[msk == 1, 0] = 255
                    img[msk == 1, 2] = 255
            if extra == '  has defect ': extra = ''
            plt.title('Train ' + train2.iloc[16 * i + k, 0] + extra)
            plt.axis('off')
            plt.imshow(img)
            plt.subplot(16, 2, 2 * k + 2)
            if dft != 0:
                msk = preds[16 * i + k, :, :, dft - 1]
                plt.imshow(msk)
            else:
                plt.imshow(np.zeros((128, 800)))
            plt.axis('off')
            mx = np.round(np.max(msk), 3)
            plt.title('Predict Defect ' + str(dft) + '  (max pixel = ' + str(mx) + ')')
        plt.subplots_adjust(wspace=0.05)
        plt.show()
    # PREDICT FROM VALIDATION SET (ONLY IMAGES WITH DEFECTS 1, 2, 4)
    val_set = train2.iloc[idx:]
    val_set2 = val_set[(val_set['count'] != 0) & (val_set['e3'] == '')].sample(16)
    valid_batches = DataGenerator(val_set2, preprocess=preprocess)
    preds = model.predict_generator(valid_batches, verbose=1)
    # PLOT PREDICTIONS
    valid_batches = DataGenerator(val_set2)
    print('Plotting predictions...')
    print('KEY: yellow=defect1, green=defect2, blue=defect3, magenta=defect4')
    for i, batch in enumerate(valid_batches):
        plt.figure(figsize=(20, 36))
        for k in range(16):
            plt.subplot(16, 2, 2 * k + 1)
            img = batch[0][k,]
            img = Image.fromarray(img.astype('uint8'))
            img = np.array(img)
            dft = 0
            three = False
            for j in range(4):
                msk = batch[1][k, :, :, j]
                if (j == 2) & (np.sum(msk) != 0):
                    three = np.sum(msk)
                msk = mask2pad(msk, pad=2)
                msk = mask2contour(msk, width=3)
                if j == 0:  # yellow
                    img[msk == 1, 0] = 235
                    img[msk == 1, 1] = 235
                elif j == 1:
                    img[msk == 1, 1] = 210  # green
                elif j == 2:
                    img[msk == 1, 2] = 255  # blue
                elif j == 3:  # magenta
                    img[msk == 1, 0] = 255
                    img[msk == 1, 2] = 255
            extra = '';
            extra2 = ''
            if not three:
                extra = 'NO DEFECT 3'
                extra2 = 'ERROR '
            plt.title('Train ' + train2.iloc[16 * i + k, 0] + '  ' + extra)
            plt.axis('off')
            plt.imshow(img)
            plt.subplot(16, 2, 2 * k + 2)
            dft = 3
            if dft != 0:
                msk = preds[16 * i + k, :, :, dft - 1]
                plt.imshow(msk)
            else:
                plt.imshow(np.zeros((128, 800)))
            plt.axis('off')
            mx = np.round(np.max(msk), 3)
            plt.title(extra2 + 'Predict Defect ' + str(dft) + '  (max pixel = ' + str(mx) + ')')
        plt.subplots_adjust(wspace=0.05)
        plt.show()


def post_porcess_threshold():
    global valid_batches, preds, i, j
    # PREDICT FROM VALIDATION SET (USE ALL)
    valid_batches = DataGenerator(train2.iloc[idx:], preprocess=preprocess)
    preds = model.predict_generator(valid_batches, verbose=1)
    # PLOT RESULTS
    pix_min = 250
    for THRESHOLD in [0.1, 0.25, 0.50, 0.75, 0.9]:
        print('######################################')
        print('## Threshold =', THRESHOLD, 'displayed below ##')
        print('######################################')
        correct = [[], [], [], []];
        incorrect = [[], [], [], []]
        for i, f in enumerate(train2.iloc[idx:idx + len(preds)]['ImageId']):
            preds2 = preds[i].copy()
            preds2[preds2 >= THRESHOLD] = 1
            preds2[preds2 < THRESHOLD] = 0
            sums = np.sum(preds2, axis=(0, 1))
            for j in range(4):
                if 4 * sums[j] < pix_min: continue
                if train2.iloc[i, j + 1] == '':
                    incorrect[j].append(4 * sums[j])
                else:
                    correct[j].append(4 * sums[j])
        plt.figure(figsize=(20, 8))
        for j in range(4):
            limit = [10000, 10000, 100000, 100000][j]
            plt.subplot(2, 2, j + 1)
            sns.distplot([x for x in correct[j] if x < limit], label='correct')
            sns.distplot([x for x in incorrect[j] if x < limit], label='incorrect')
            plt.title('Defect ' + str(j + 1) + ' mask sizes with threshold = ' + str(THRESHOLD))
            plt.legend()
        plt.show()
        for j in range(4):
            c1 = np.array(correct[j])
            c2 = np.array(incorrect[j])
            print('With threshold =', THRESHOLD, ', defect', j + 1, 'has', len(c1[c1 != 0]), 'correct and',
                  len(c2[c2 != 0]), 'incorrect masks')
        print()


if __name__ == '__main__':
    # model = load_model('UNET.h5',custom_objects={'dice_coef':dice_coef})
    warnings.filterwarnings("ignore")
    data_prep()
    data_inspection()
    network_setup()
    network_inspection()
    post_porcess_threshold()

    # # PREDICT 1 BATCH TEST DATASET
    test = pd.read_csv(path + 'sample_submission.csv')
    test['ImageId'] = test['ImageId_ClassId'].map(lambda x: x.split('_')[0])
    test_batches = DataGenerator(test.iloc[::4], subset='test', preprocess=preprocess)
    test_preds = model.predict_generator(test_batches, steps=1, verbose=1)

    # SAVE MODEL
    model.save('UNET.h5')
    # # LOAD MODEL
    # from keras.models import load_model
    # model = load_model('UNET.h5',custom_objects={'dice_coef':dice_coef})

    # # PREDICT 1 BATCH TEST DATASET
    # test = pd.read_csv(path + 'sample_submission.csv')
    # test['ImageId'] = test['ImageId_ClassId'].map(lambda x: x.split('_')[0])
    # test_batches = DataGenerator(test.iloc[::4],subset='test',batch_size=256,preprocess=preprocess)
    # test_preds = model.predict_generator(test_batches,steps=1,verbose=1)

    # # NEXT CONVERT MASKS TO RLE, ADD TO CSV, PROCESS REMAINING BATCHES, AND SUBMIT !!
