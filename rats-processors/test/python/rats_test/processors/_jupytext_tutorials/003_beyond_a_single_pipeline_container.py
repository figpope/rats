# type: ignore
# there are multiple typing issues in pandas and the iris dataset, easier to disable typing for
# this notebook

# %% [markdown]
# So far we have learned how to create self-contained pipelines in a single container class.
#
# In this notebook we will learn how to expose and use pipelines across containers.
#
# %% [markdown]
# ### Using pipelines defined in installed packages.
#
# The LR pipelines we have defined in [the previous notebook](002_lr_using_sklearn.md) are also
# defined in `rats.processors.example_pipelines`.  We will start by showing how to access these
# pipelines from the app.
# %%
from typing import NamedTuple, cast

import pandas as pd

from rats import apps
from rats import processors as rp
from rats.processors import example_pipelines as rpe
from rats.processors import typing as rpt

# %% [markdown]
# As noted, the pipeline decorators `task` and `pipeline` register the methods as services.
# To access those services we need their service ids.  For public services, these are added to a
# `Services` class, such as `rpe.Services`.

# %%
[p for p in dir(rpe.Services) if not p.startswith("_")]

# %% [markdown]
# All of these properties are `ServiceId` instances, which can be used to get services from
# containers.
#
# Recall that before, to get a pipeline, we used the `get` method of the container. The containers
# that create these pipelines in `example_pipelines` are registered with the notebook app (more on
# that below), so we can access these pipeline by calling the `get` method of the app with their
# service id.
# %%
app = rp.NotebookApp()
train_pipeline = app.get(rpe.Services.LR_TRAIN_PIPELINE)
print("train pipeline input ports:", train_pipeline.inputs)
print("train pipeline output ports:", train_pipeline.outputs)
app.display(train_pipeline)
# %% [markdown]
# Note:
# Like all services, in a statically typed environment, the service ids carry the static type of
# the service they represent.  Here, train_pipeline is of static type
# `Pipeline[TrainPipelineInputs, TrainPipelineOutputs]`.


# %% [markdown]
# ### Registering a container with the notebook app.
#
# The mechanisms for registering containers to apps are different when registering in a notebook
# versus in a package.  We will discuss both below.
#
# To illustrate registering a container to an app in a notebook we will create a new container
# with a pipeline for splitting a dataframe into training and testing sets.
#
# To illustrate using registered pipeline services as sub-pipelines, we will create another
# container using the new `split_data` pipeline, as well as `train_pipeline` and `predict_pipeline`
# into a `train_and_predict_pipeline`.
#
# But first the `split_data` pipeline container:
# %%
class _SplitLabelsOutput(NamedTuple):
    train_labels: pd.Series
    test_labels: pd.Series


class _LimitSamplesOutput(NamedTuple):
    samples: pd.DataFrame


class SplitDataInputs(rpt.Inputs):
    frac_train: rpt.InPort[float]
    seed: rpt.InPort[int]
    labels: rpt.InPort[pd.Series]
    samples: rpt.InPort[pd.DataFrame]


class SplitDataOutputs(rpt.Outputs):
    train_samples: rpt.OutPort[pd.DataFrame]
    train_labels: rpt.OutPort[pd.Series]
    test_samples: rpt.OutPort[pd.DataFrame]
    test_labels: rpt.OutPort[pd.Series]


SplitDataPl = rpt.Pipeline[SplitDataInputs, SplitDataOutputs]


class SplitDataPipelineContainer(rp.PipelineContainer):
    @rp.task
    def split_labels(self, frac_train: float, seed: int, labels: pd.Series) -> _SplitLabelsOutput:
        train_indices = labels.sample(frac=frac_train, random_state=seed).index
        train_labels = labels.loc[train_indices]
        test_labels = labels.drop(train_indices)
        return _SplitLabelsOutput(
            train_labels=train_labels,
            test_labels=test_labels,
        )

    @rp.task
    def limit_samples(self, labels: pd.Series, samples: pd.DataFrame) -> _LimitSamplesOutput:
        return _LimitSamplesOutput(samples=samples.loc[labels.index])

    @rp.pipeline
    def limit_train_samples(self) -> rpt.UPipeline:
        # create a copy of limit_samples with inputs and outputs renamed to indicated that it is
        # to be used for train.
        return (
            self.get(apps.autoid(self.limit_samples))
            .rename_inputs(dict(labels="train_labels"))
            .rename_outputs(dict(samples="train_samples"))
        )

    @rp.pipeline
    def limit_test_samples(self) -> rpt.UPipeline:
        # create a copy of limit_samples with inputs and outputs renamed to indicated that it is
        # to be used for test.
        return (
            self.get(apps.autoid(self.limit_samples))
            .rename_inputs(dict(labels="test_labels"))
            .rename_outputs(dict(samples="test_samples"))
        )

    @rp.pipeline
    def split_data(self) -> SplitDataPl:
        split_labels = self.get(apps.autoid(self.split_labels))
        limit_train_samples = self.get(apps.autoid(self.limit_train_samples))
        limit_test_samples = self.get(apps.autoid(self.limit_test_samples))
        return cast(
            SplitDataPl,
            self.combine(
                pipelines=[split_labels, limit_train_samples, limit_test_samples],
                dependencies=(
                    split_labels >> limit_train_samples,
                    split_labels >> limit_test_samples,
                ),
                # We want to expose train_labels and test_labels, but they are already consumed by
                # limit_train_samples and limit_test_samples, so are not exposed by default.
                # Therefore we need to provide the outputs explicitly.
                outputs=dict(
                    train_samples=limit_train_samples.outputs.train_samples,
                    test_samples=limit_test_samples.outputs.test_samples,
                    train_labels=split_labels.outputs.train_labels,
                    test_labels=split_labels.outputs.test_labels,
                ),
            ),
        )


class SplitDataServices:
    LR_SPLIT_DATA_PIPELINE = apps.autoid(SplitDataPipelineContainer.split_data)


# %% [markdown]
# We want to make the split-data pipeline available to other containers. To that end, we need to
# expose a service id for the pipeline in public class - `SplitDataServices` above.
#
# We also need to register `SplitDataPipelineContainer` with the app. In a notebook, registering a
# pipeline container with the app requires re-creating the app instance, passing a callable taking
# a container and returning a container to its constructor.  The app will pass itself to that
# callable, and register the returned container with itself.
#
# Much easier understand from code:
# %%
app = rp.NotebookApp(lambda app: SplitDataPipelineContainer())
split_data_pipeline = app.get(SplitDataServices.LR_SPLIT_DATA_PIPELINE)
print("split data pipeline input ports:", split_data_pipeline.inputs)
print("split data pipeline output ports:", split_data_pipeline.outputs)
app.display(split_data_pipeline)


# %% [markdown]
# ### Using pipelines registered with the app as sub-pipelines in another container.
#
# As promised, we will now create a container that uses `split_data_pipeline`, `train_pipeline`
# and `predict_pipeline` to create a `train_and_predict_pipeline`.
#
# Our new container will need access to the app, and hence will take it in its constructor. Note
# that the app itself is a container, and to be general, we will use the `apps.Container` type.
#
# Note that we wrap each external sub-pipeline with a `@pipeline` method.  This ensures that these
# pipelines are distincly named within the context of the container.
# %%


class _EvaluateOutput(NamedTuple):
    evaluation: pd.DataFrame


class TrainAndPredictPipelineContainer(rp.PipelineContainer):
    _app: apps.Container

    def __init__(self, app: apps.Container):
        super().__init__()
        self.app = app

    @rp.pipeline
    def split_data(self) -> rpt.UPipeline:
        return self.app.get(SplitDataServices.LR_SPLIT_DATA_PIPELINE)

    @rp.pipeline
    def train(self) -> rpt.UPipeline:
        return self.app.get(rpe.Services.LR_TRAIN_PIPELINE).rename_inputs(
            dict(x="train_samples", y="train_labels")
        )

    @rp.pipeline
    def predict_on_train(self) -> rpt.UPipeline:
        return (
            self.app.get(rpe.Services.LR_PREDICT_PIPELINE)
            .rename_inputs(dict(x="train_samples"))
            .rename_outputs(dict(logits="train_logits"))
        )

    @rp.pipeline
    def predict_on_test(self) -> rpt.UPipeline:
        return (
            self.app.get(rpe.Services.LR_PREDICT_PIPELINE)
            .rename_inputs(dict(x="test_samples"))
            .rename_outputs(dict(logits="test_logits"))
        )

    @rp.task
    def evaluate(self, logits: pd.DataFrame, labels: pd.Series) -> _EvaluateOutput:
        evaluation = logits.join(labels).groupby(labels.name).agg(["mean", "std", "count"])
        return _EvaluateOutput(evaluation=evaluation)

    @rp.pipeline
    def evaluate_on_train(self) -> rpt.UPipeline:
        return (
            self.get(apps.autoid(self.evaluate))
            .rename_inputs(dict(logits="train_logits", labels="train_labels"))
            .rename_outputs(dict(evaluation="train_evaluation"))
        )

    @rp.pipeline
    def evaluate_on_test(self) -> rpt.UPipeline:
        return (
            self.get(apps.autoid(self.evaluate))
            .rename_inputs(dict(logits="test_logits", labels="test_labels"))
            .rename_outputs(dict(evaluation="test_evaluation"))
        )

    @rp.pipeline
    def train_and_predict_pipeline(self) -> rpt.UPipeline:
        split_data = self.get(apps.autoid(self.split_data))
        train = self.get(apps.autoid(self.train))
        predict_on_train = self.get(apps.autoid(self.predict_on_train))
        predict_on_test = self.get(apps.autoid(self.predict_on_test))
        evaluate_on_train = self.get(apps.autoid(self.evaluate_on_train))
        evaluate_on_test = self.get(apps.autoid(self.evaluate_on_test))
        return self.combine(
            pipelines=[
                split_data,
                train,
                predict_on_train,
                predict_on_test,
                evaluate_on_train,
                evaluate_on_test,
            ],
            dependencies=(
                split_data >> train,
                split_data >> predict_on_train,
                split_data >> predict_on_test,
                split_data >> evaluate_on_train,
                split_data >> evaluate_on_test,
                train >> predict_on_train,
                train >> predict_on_test,
                predict_on_train >> evaluate_on_train,
                predict_on_test >> evaluate_on_test,
            ),
        )


class TrainAndPredictPipelineServices:
    LR_TRAIN_AND_PREDICT_PIPELINE = apps.autoid(
        TrainAndPredictPipelineContainer.train_and_predict_pipeline
    )


# %% [markdown]
# We'll recreate the app again, this time registering both `SplitDataPipelineContainer` and
# `TrainAndPredictPipelineContainer`.

# %%
app = rp.NotebookApp(
    lambda app: SplitDataPipelineContainer(),
    TrainAndPredictPipelineContainer,  # no need to wrap with lambda b/c the ctor takes an app
)
# %%

train_and_predict_pipeline = app.get(TrainAndPredictPipelineServices.LR_TRAIN_AND_PREDICT_PIPELINE)
print("train and predict pipeline input ports:", train_and_predict_pipeline.inputs)
print("train and predict pipeline output ports:", train_and_predict_pipeline.outputs)
app.display(train_and_predict_pipeline)
# %% [markdown]
# Let's load the iris data and run!
# %%
from sklearn import datasets

iris = datasets.load_iris()
category_names = tuple(iris["target_names"])

samples = pd.DataFrame(iris["data"], columns=iris["feature_names"])
labels = pd.Series(iris["target"], name="label").map(lambda i: category_names[i])

train_and_predict_outputs = app.run(
    train_and_predict_pipeline,
    inputs=dict(
        frac_train=0.8,
        seed=6001986,
        labels=labels,
        samples=samples,
        category_names=category_names,
    ),
)
# %% [markdown]
# Train evaluation:
# %%
train_and_predict_outputs["train_evaluation"]
# %% [markdown]
# Test evaluation:
# %%
train_and_predict_outputs["test_evaluation"]


# %% [markdown]
# ### Registering containers in panckages.
# As mentioned above, the mechanism for registering containers defined in a package (i.e.) in your
# repo with the `rats.processors` apps is different.
#
# We will explain them here, but cannot demonstrate them in a notebook.  There are two parts to the
# mechanism:
#
# #### Registering a container within another container using the `@container` decorator.
# The `@container` decorator allows a container to include other containers as sub-containers. A
# `.get(service_id)` call on the container will search it and all the containers it includes via a
# `@container` decorator.
#
# For example, we could create antoher container class that includes `SplitDataPipelineContainer` and
# `TrainAndPredictPipelineContainer` as sub-containers, and register that other container class
# with the app.  Functionally, this is the same as what we did above:
# %%
class C1(apps.AnnotatedContainer):
    _app: apps.Container

    def __init__(self, app: apps.Container) -> None:
        super().__init__()
        self._app = app

    @apps.container()
    def split_data(self) -> SplitDataPipelineContainer:
        return SplitDataPipelineContainer()

    @apps.container()
    def train_and_predict(self) -> TrainAndPredictPipelineContainer:
        return TrainAndPredictPipelineContainer(self._app)


app = rp.NotebookApp(C1)

train_and_predict_pipeline = app.get(TrainAndPredictPipelineServices.LR_TRAIN_AND_PREDICT_PIPELINE)
print("train and predict pipeline input ports:", train_and_predict_pipeline.inputs)
print("train and predict pipeline output ports:", train_and_predict_pipeline.outputs)
app.display(train_and_predict_pipeline)
# %% [markdown]
# We recommend using this mechanism to create a top level container for every package.
# For example, in the `rats.processors` package, we have a private
# `rats.processors._plugin_container.PluginContainer` that includes (directly or indirectly) all
# other containers in the package.
#
# #### Registering containers to the app via the python `entry_points` mechanism.
#
# We want to enable client packages (like yours) to register their services with the
# `rats.processors` app classes (`NotebookApp`, `CliApp`).  Obviously, the app classes cannot know
# about client package container.  Instead, the app classes interact with the python entry point
# mechanism to discover import paths of container classes that want to register with them.
#
# The entry point group used by `rats.processors` apps is "rats.processors_app_plugins".
#
# Using `poetry` and `pyproject.toml`, you can register your container classes with the apps by
# adding the following to your `pyproject.toml`:
# ```
# [tool.poetry.plugins."rats.processors_app_plugins"]
# "my_package" = "my_package.my_module:MyContainer"
# ```
# The only two requirements are that `my_package.my_module:MyContainer` implements the
# `apps.Container` interface (e.g. by inheriting from `apps.AnnotatedContainer` or
# `rp.PipelineContainer`), and that it's constructor takes an `apps.Container` as its only
# argument.
#
# For example, in the `rats.processors` package, we self register our top level container like
# this:
# ```
# [tool.poetry.plugins."rats.processors_app_plugins"]
# "rats.processors" = "rats.processors._plugin_container:PluginContainer"
# ```
