#==============================================================================#
#  Author:       Dominik Müller                                                #
#  Copyright:    2019 IT-Infrastructure for Translational Medical Research,    #
#                University of Augsburg                                        #
#                                                                              #
#  This program is free software: you can redistribute it and/or modify        #
#  it under the terms of the GNU General Public License as published by        #
#  the Free Software Foundation, either version 3 of the License, or           #
#  (at your option) any later version.                                         #
#                                                                              #
#  This program is distributed in the hope that it will be useful,             #
#  but WITHOUT ANY WARRANTY; without even the implied warranty of              #
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the               #
#  GNU General Public License for more details.                                #
#                                                                              #
#  You should have received a copy of the GNU General Public License           #
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.       #
#==============================================================================#
#-----------------------------------------------------#
#                   Library imports                   #
#-----------------------------------------------------#
# External libraries
from keras.utils import multi_gpu_model
from keras.optimizers import Adam
from keras.models import model_from_json
import numpy as np
# Internal libraries/scripts
from miscnn.neural_network.metrics import dice_classwise, tversky_loss
from miscnn.neural_network.architecture.unet.standard import Architecture
from miscnn.neural_network.data_generator import DataGenerator
from miscnn.utils.patch_operations import concat_3Dmatrices

#-----------------------------------------------------#
#            Neural Network (model) class             #
#-----------------------------------------------------#
# Class which represents the Neural Network and which run the whole pipeline
class Neural_Network:
    """ Initialization function for creating a Neural Network (model) object.
    This class provides functionality for handling all model methods.
    This class runs the whole pipeline and uses a Preprocessor instance to obtain batches.

    With an initialized Neural Network model instance, it is possible to run training, prediction
    and evaluations.

    Args:
        preprocessor (Preprocessor):            Preprocessor class instance which provides the Neural Network with batches.
        architecture (Architecture):            Instance of a neural network model Architecture class instance.
                                                By default, a standard U-Net is used as Architecture.
        loss (Metric Function):                 The metric function which is used as loss for training.
                                                Any Metric Function defined in Keras, in miscnn.neural_network.metrics or any custom
                                                metric function, which follows the Keras metric guidelines, can be used.
        metrics (List of Metric Functions):     List of one or multiple Metric Functions, which will be shown during training.
                                                Any Metric Function defined in Keras, in miscnn.neural_network.metrics or any custom
                                                metric function, which follows the Keras metric guidelines, can be used.
        epochs (integer):                       Number of epochs. A single epoch is defined as one iteration through the complete data set.
        learning_rate (float):                  Learning rate in which weights of the neural network will be updated.
        batch_queue_size (integer):             The batch queue size is the number of previously prepared batches in the cache during runtime.
        gpu_number (integer):                   Number of GPUs, which will be used for training.
    """
    def __init__(self, preprocessor, architecture=Architecture(),
                 loss=tversky_loss, metrics=[dice_classwise],
                 epochs=20, learninig_rate=0.0001,
                 batch_queue_size=2, gpu_number=1):
        # Identify data parameters
        three_dim = preprocessor.data_io.interface.three_dim
        channels = preprocessor.data_io.interface.channels
        classes = preprocessor.data_io.interface.classes
        # Assemble the input shape
        input_shape = (None,)
        # Initialize model for 3D data
        if three_dim:
            input_shape = (None, None, None, channels)
            self.model = architecture.create_model_3D(input_shape=input_shape,
                                                      n_labels=classes)
         # Initialize model for 2D data
        else:
             input_shape = (None, None, channels)
             self.model = architecture.create_model_2D(input_shape=input_shape,
                                                       n_labels=classes)
        # Transform to Keras multi GPU model
        if gpu_number > 1:
            self.model = multi_gpu_model(self.model, gpu_number)
        # Compile model
        self.model.compile(optimizer=Adam(lr=learninig_rate),
                           loss=loss, metrics=metrics)
        # Cache starting weights
        self.initialization_weights = self.model.get_weights()
        # Cache parameter
        self.preprocessor = preprocessor
        self.loss = loss
        self.metrics = metrics
        self.epochs = epochs
        self.learninig_rate = learninig_rate
        self.batch_queue_size = batch_queue_size

    #---------------------------------------------#
    #               Class variables               #
    #---------------------------------------------#
    shuffle_batches = True                  # Option whether batch order should be shuffled or not
    initialization_weights = None           # Neural Network model weights for weight reinitialization

    #---------------------------------------------#
    #                  Training                   #
    #---------------------------------------------#
    """ Fitting function for the Neural Network model using the provided list of sample indices.

    Args:
        sample_list (list of indices):  A list of sample indicies which will be used for training
    """
    def train(self, sample_list):
        # Initialize Keras Data Generator for generating batches
        dataGen = DataGenerator(sample_list, self.preprocessor, training=True,
                                validation=False, shuffle=self.shuffle_batches)
        # Run training process with Keras fit_generator
        self.model.fit_generator(generator=dataGen,
                                 epochs=self.epochs,
                                 max_queue_size=self.batch_queue_size)
        # Clean up temporary files if necessary
        if self.preprocessor.prepare_batches or self.preprocessor.prepare_subfunctions:
            self.preprocessor.data_io.batch_cleanup()

    #---------------------------------------------#
    #                 Prediction                  #
    #---------------------------------------------#
    """ Prediction function for the Neural Network model. The fitted model will predict a segmentation
        for the provided list of sample indices.

    Args:
        sample_list (list of indices):  A list of sample indicies for which a segmentation prediction will be computed
        direct_output (boolean):        Parameter which decides, if computed predictions will be output as the return of this
                                        function or if the predictions will be saved with the save_prediction method defined
                                        in the provided Data I/O interface.
    """
    def predict(self, sample_list, direct_output=False):
        # Initialize result array for direct output
        if direct_output : results = []
        # Iterate over each sample
        for sample in sample_list:
            # Initialize Keras Data Generator for generating batches
            dataGen = DataGenerator([sample], self.preprocessor,
                                    training=False, validation=False,
                                    shuffle=False)
            # Run prediction process with Keras predict_generator
            pred_seg = self.model.predict_generator(
                                     generator=dataGen,
                                     max_queue_size=self.batch_queue_size)
            # Reassemble patches into original shape for patchwise analysis
            if self.preprocessor.analysis == "patchwise-crop" or \
                self.preprocessor.analysis == "patchwise-grid":
                # Load cached shape
                seg_shape = self.preprocessor.shape_cache.pop(sample)
                # Concatenate patches into original shape
                pred_seg = concat_3Dmatrices(
                               patches=pred_seg,
                               image_size=seg_shape,
                               window=self.preprocessor.patch_shape,
                               overlap=self.preprocessor.patchwise_grid_overlap)
            # Transform probabilities to classes
            pred_seg = np.argmax(pred_seg, axis=-1)
            # Run Subfunction postprocessing on the prediction
            for sf in self.preprocessor.subfunctions:
                sf.postprocessing(pred_seg)
            # Backup predicted segmentation
            if direct_output : results.append(pred_seg)
            else : self.preprocessor.data_io.save_prediction(pred_seg, sample)
            # Clean up temporary files if necessary
            if self.preprocessor.prepare_batches or self.preprocessor.prepare_subfunctions:
                self.preprocessor.data_io.batch_cleanup()
        # Output predictions results if direct output modus is active
        if direct_output : return results

    #---------------------------------------------#
    #                 Evaluation                  #
    #---------------------------------------------#
    """ Evaluation function for the Neural Network model using the provided lists of sample indices
        for training and validation. It is also possible to pass custom Callback classes in order to
        obtain more information.

    Args:
        training_samples (list of indices):     A list of sample indicies which will be used for training
        validation_samples (list of indices):   A list of sample indicies which will be used for validation
        callbacks (list of Callback classes):   A list of Callback classes for custom evaluation

    Return:
        history (Keras history object):         Gathered fitting information and evaluation results of the validation
    """
    # Evaluate the Neural Network model using the MIScnn pipeline
    def evaluate(self, training_samples, validation_samples, callbacks=[]):
        # Initialize a Keras Data Generator for generating Training data
        dataGen_training = DataGenerator(training_samples, self.preprocessor,
                                         training=True, validation=False,
                                         shuffle=self.shuffle_batches)
        # Initialize a Keras Data Generator for generating Validation data
        dataGen_validation = DataGenerator(validation_samples,
                                           self.preprocessor,
                                           training=True, validation=True,
                                           shuffle=self.shuffle_batches)
        # Run training & validation process with the Keras fit_generator
        history = self.model.fit_generator(generator=dataGen_training,
                                 validation_data=dataGen_validation,
                                 callbacks=callbacks,
                                 epochs=self.epochs,
                                 max_queue_size=self.batch_queue_size)
        # Clean up temporary files if necessary
        if self.preprocessor.prepare_batches or self.preprocessor.prepare_subfunctions:
            self.preprocessor.data_io.batch_cleanup()
        # Return the training & validation history
        return history

    #---------------------------------------------#
    #               Model Management              #
    #---------------------------------------------#
    # Re-initialize model weights
    def reset_weights(self):
        self.model.set_weights(self.initialization_weights)

    # Dump model to file
    def dump(self, file_path):
        # Create model output path
        outpath_model = file_path + ".model.json"
        outpath_weights = file_path + ".weights.h5"
        # Serialize model to JSON
        model_json = self.model.to_json()
        with open(outpath_model, "w") as json_file:
            json_file.write(model_json)
        # Serialize weights to HDF5
        self.model.save_weights(outpath_weights)

    # Load model from file
    def load(self, file_path):
        # Create model input path
        inpath_model = file_path + ".model.json"
        inpath_weights = file_path + ".weights.h5"
        # Load json and create model
        json_file = open(inpath_model, 'r')
        loaded_model_json = json_file.read()
        json_file.close()
        self.model = model_from_json(loaded_model_json)
        # Load weights into new model
        self.model.load_weights(inpath_weights)
        # Compile model
        self.model.compile(optimizer=Adam(lr=self.learninig_rate),
                           loss=self.loss, metrics=self.metrics)
