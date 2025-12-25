"""
A production-quality test filtering pipeline for pytest.

This module provides a flexible, extensible architecture for filtering pytest
test collections through a series of configurable stages.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
import pytest


class PipelineStep(ABC):
    """
    Abstract base class for all test filter steps.

    Each concrete step must implement the apply method to process a list
    of pytest.Item objects and return a filtered or transformed list.
    """
    
    @abstractmethod
    def apply(self, tests: List[pytest.Item]) -> Union[List[pytest.Item], Any]:
        """
        Apply filtering logic to the provided test items.
        
        Args:
            tests: List of pytest.Item objects to process
            
        Returns:
            Filtered list of pytest.Item objects or transformed data structure
        """
        pass

#TODO check if necessary
class TestFilterPipeline:
    """
    A pipeline that applies multiple test filter stages sequentially.
    
    Each stage receives the output of the previous stage, allowing for
    complex filtering workflows through composition.
    """
    
    def __init__(self, stages: Optional[List[PipelineStep]] = None) -> None:
        """
        Initialize the pipeline with an optional list of stages.
        
        Args:
            stages: List of PipelineStep instances to apply in order
        """
        self._stages: List[PipelineStep] = stages or []
    
    def add_stage(self, stage: PipelineStep) -> "TestFilterPipeline":
        """
        Add a filter stage to the end of the pipeline.
        
        Args:
            stage: PipelineStep instance to add
            
        Returns:
            Self for method chaining
        """
        self._stages.append(stage)
        return self
    
    def apply(self, tests: List[pytest.Item]) -> Any:
        """
        Apply all stages in the pipeline sequentially.
        
        Args:
            tests: Initial list of pytest.Item objects
            
        Returns:
            Result after applying all stages (may be filtered tests or other data)
        """
        result = tests
        for stage in self._stages:
            result = stage.apply(result)
        return result
    
    def clear(self) -> None:
        """Remove all stages from the pipeline."""
        self._stages.clear()
    
    @property
    def stages(self) -> List[PipelineStep]:
        """Get a copy of the current stages list."""
        return self._stages.copy()


class DateFilterStage(PipelineStep):
    """
    Filter stage that keeps tests based on day-of-week scheduling.
    
    Tests can be marked with @pytest.mark.run_days(...) to specify
    when they should run. Supports explicit weekdays and "weekend" keyword.
    """
    
    # Mapping of weekday names to datetime.weekday() values (Monday=0)
    WEEKDAY_MAP = {
        'sun': 0, 'sunday': 0,
        'mon': 1, 'monday': 1,
        'tue': 2, 'tuesday': 2,
        'wed': 3, 'wednesday': 3,
        'thu': 4, 'thursday': 4,
        'fri': 5, 'friday': 5,
        'sat': 6, 'saturday': 6
    }

    WEEKEND_DAYS = {5, 6}  # Friday, Saturday
    
    def __init__(self, current_time: Optional[datetime] = None) -> None:
        """
        Initialize the date filter stage.
        
        Args:
            current_time: Override current time for testing (defaults to now)
        """
        self._current_time = current_time
    
    def apply(self, tests: List[pytest.Item]) -> List[pytest.Item]:
        """
        Filter tests based on run_days marker and current day.
        
        Args:
            tests: List of pytest.Item objects to filter
            
        Returns:
            Filtered list containing only tests that should run today
        """
        current_day = (self._current_time or datetime.now()).weekday()
        filtered_tests = []
        
        for test in tests:
            if self._should_run_today(test, current_day):
                filtered_tests.append(test)
        
        return filtered_tests
    
    def _should_run_today(self, test: pytest.Item, current_day: int) -> bool:
        """
        Check if a test should run on the current day.
        
        Args:
            test: pytest.Item to check
            current_day: Current weekday (0=Monday, 6=Sunday)
            
        Returns:
            True if test should run today, False otherwise
        """
        run_days_mark = test.get_closest_marker('run_days')
        if not run_days_mark:
            # No marker means run always
            return True
        
        allowed_days = set()
        
        # Process marker arguments
        for arg in run_days_mark.args:
            if isinstance(arg, str):
                arg_lower = arg.lower()
                if arg_lower == 'weekend':
                    allowed_days.update(self.WEEKEND_DAYS)
                elif arg_lower in self.WEEKDAY_MAP:
                    allowed_days.add(self.WEEKDAY_MAP[arg_lower])
        
        # Process marker keyword arguments
        for key, value in getattr(run_days_mark, "kwargs", {}).items():
            if key.lower() == 'days' and isinstance(value, (list, tuple)):
                for day in value:
                    if isinstance(day, str):
                        day_lower = day.lower()
                        if day_lower == 'weekend':
                            allowed_days.update(self.WEEKEND_DAYS)
                        elif day_lower in self.WEEKDAY_MAP:
                            allowed_days.add(self.WEEKDAY_MAP[day_lower])
        
        return current_day in allowed_days if allowed_days else True


@dataclass
class FixtureParameterGroups:
    """Data structure holding grouped test results."""
    groups: Dict[str, List[pytest.Item]]
    unmatched: List[pytest.Item]
    
    def get_group(self, group_name: str) -> List[pytest.Item]:
        """Get tests for a specific group by name."""
        return self.groups.get(group_name, [])
    
    def get_all_groups(self) -> Dict[str, List[pytest.Item]]:
        """Get all groups as a dictionary."""
        return self.groups.copy()
    
    def get_group_names(self) -> List[str]:
        """Get list of all group names."""
        return list(self.groups.keys())


class FixtureParameterGroupingStage(PipelineStep):
    """
    Stage that groups tests based on fixture parameter values.
    Example:
        - Group "fast" includes tests which have fixture that has parameters "mode_a" or "quick"
        - Group "slow" includes tests which have fixture that has parameters "mode_b" or "detailed"
    
    Inspects parametrized fixtures and groups tests based on parameter values
    matching configured patterns. Supports any number of named groups.

    Same test may belong to multiple groups, depending on its fixture parameter values !
    Example:
       test_foo(some_fixture): ...

       @pytest.mark.parametrize(["quick", "slow"])
       def test_foo(request):
           ....

        test_foo belongs to group "fast" when the fixture parameter is "quick" but not when it is "slow".
    """
    
    def __init__(
        self,
        fixture_name: str,
        group_mappings: Dict[str, List[str]]
    ) -> None:
        """
        Initialize the fixture parameter grouping stage.
        
        Args:
            fixture_name: Name of the fixture to inspect for parameters
            group_mappings: Dictionary mapping group names to lists of parameter values
                          e.g., {"fast": ["mode_a", "quick"], "slow": ["mode_b", "detailed"]}
        """
        self.fixture_name = fixture_name
        self.group_mappings = {
            group_name: set(values) 
            for group_name, values in group_mappings.items()
        }
        
        # Create reverse mapping for efficient lookup
        self._value_to_group = {}
        for group_name, values in self.group_mappings.items():
            for value in values:
                self._value_to_group[value] = group_name
    
    def apply(self, tests: List[pytest.Item]) -> FixtureParameterGroups:
        """
        Group tests based on fixture parameter values.
        
        Args:
            tests: List of pytest.Item objects to group
            
        Returns:
            The function does not return anything, but adds a marker to the tests, according to their group.
        """
        groups = {group_name: [] for group_name in self.group_mappings.keys()}
        unmatched = []
        
        for test in tests:
            parameter_value = self._get_fixture_parameter_value(test)
            
            if parameter_value and parameter_value in self._value_to_group:
                group_name = self._value_to_group[parameter_value]
                groups[group_name].append(test)
            else:
                unmatched.append(test)
        
        return FixtureParameterGroups(
            groups=groups,
            unmatched=unmatched
        )
    
    def _get_fixture_parameter_value(self, test: pytest.Item) -> Optional[str]:
        """
        Extract the parameter value for the specified fixture from a test.
        
        Args:
            test: pytest.Item to inspect
            
        Returns:
            Parameter value as string, or None if not found
        """
        # Check if test has callspec (parametrized)
        if not hasattr(test, 'callspec'):
            return None
        
        callspec = test.callspec
        if not callspec or not hasattr(callspec, 'params'):
            return None
        
        # Look for our fixture in the parameters
        if self.fixture_name in callspec.params:
            param_value = callspec.params[self.fixture_name]
            return str(param_value) if param_value is not None else None
        
        return None


# Example usage in pytest_collection_modifyitems hook
def pytest_collection_modifyitems(config: pytest.Config, items: List[pytest.Item]) -> None:
    """
    Example pytest hook showing how to use the test filter pipeline.
    
    This hook is called after collection is completed and allows modification
    of the collected test items.
    """
    # Create and configure the pipeline
    pipeline = TestFilterPipeline()
    
    # Add date filtering stage
    date_filter = DateFilterStage()
    pipeline.add_stage(date_filter)
    
    # Apply the pipeline - for date filtering, we want the filtered list
    filtered_items = pipeline.apply(items)
    
    # Update the items list in-place
    items[:] = filtered_items
    
    # Example of using the grouping stage separately
    grouping_stage = FixtureParameterGroupingStage(
        fixture_name="test_mode",
        group_mappings={
            "fast": ["mode_a", "quick"],
            "slow": ["mode_b", "detailed"],
            "experimental": ["mode_c", "beta"]
        }
    )
    
    groups = grouping_stage.apply(items)
    
    # Example: run only fast tests if a specific marker is present
    if config.getoption("--run-fast-only", default=False):
        items[:] = groups.get_group("fast")
    
    # Example: run specific group based on command line option
    target_group = config.getoption("--group", default=None)
    if target_group and target_group in groups.get_group_names():
        items[:] = groups.get_group(target_group)
