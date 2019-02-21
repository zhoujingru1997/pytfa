#!/usr/bin/env python
# -*- coding: utf-8 -*-

from ..io.base import import_matlab_model, load_thermoDB
from cobra.io import load_json_model, load_yaml_model, read_sbml_model
from ..optim.utils import symbol_sum

from ..optim.variables import BinaryVariable
from ..thermo.tmodel import ThermoModel

from numpy import sum

CPLEX = 'optlang-cplex'
GUROBI = 'optlang-gurobi'
GLPK = 'optlang-glpk'


class LumpGEM:
    """
    A class encapsulating the LumpGEM algorithm
    """
    def __init__(self, path_to_model, biomass_rxns, core_subsystems, carbon_uptake, growth_rate,  thermo_data_path):
        """
        : param GEM: the GEM 
        : type GEM: cobra model

        : param biomass_rxns: list of biomass reactions
        : type biomass_rxns: [GEM.biomass_rxn.id]

        : param core_subsystems: list of Core subsystems names
        : type core_subsystems: [string]

        : param carbon_intake: the amount of carbon atoms the cell intakes from its surrounding
        : type carbon_intake: float

        : param growth_rate: theoretical maximum specific growth rate in 1/hr units
        : type growth_rate: float

        : param thermo_data_path: the path to the .thermodb database
        : type thermo_data_path : string
        """

        # Load the GEM through the appropriate cobra loading function (based on path suffix)
        model = self._load_model(path_to_model)
        # Build thermo model
        self._tfa_model = self._apply_thermo_constraints(thermo_data_path, model)

        # Set containing every core reaction
        self._rcore = set([])
        # Set containing every core metabolite
        self._mcore = set([])
        # Set containing every non-core reaction
        self._rncore = set([])

        # For each reaction
        for rxn in model.reactions:
            # If it's a BBB reaction
            if rxn.id in biomass_rxns:
                self._rBBB.add(rxn)
            # If it's a core reaction
            elif rxn.subsystem in core_subsystems:
                self._rcore.add(rxn)
                for met in rxn.metabolites:
                    self._mcore.add(met)
            # If it's neither BBB nor core, then it's non-core
            else:
                self._rncore.add(rxn)

        # Carbon uptake
        self._C_uptake = carbon_uptake
        # Growth rate
        self._growth_rate = growth_rate

        # TODO : solver choice
        self._solver = 'optlang-cplex'

        self._bin_vars = self._generate_binary_variables()
        self._generate_constraints()
        self._generate_objective()

    def _load_model(self, path_to_model):
        # Matlab
        if path_to_model[-4:] == ".mat":
            return import_matlab_model(path_to_model)

        # YAML
        if path_to_model[-4:] == ".yml":
            return load_yaml_model(path_to_model)

        # JSON
        if path_to_model[-5:] == ".json":
            return load_json_model(path_to_model)

        # SBML
        if path_to_model[-4:] == ".xml":
            return read_sbml_model(path_to_model)

    def _apply_thermo_constraints(self, thermo_data_path, cobra_model):
        """
        Apply thermodynamics constraints defined in thermoDB to Mcore & Rcore
        """
        thermo_data = load_thermoDB(thermo_data_path)
        tfa_model = ThermoModel(thermo_data, cobra_model)
        tfa_model.name = 'Lumped Model'

        # TODO : Check what are these operations for
        # self.read_lexicon = read_lexicon()
        # compartment_data = read_compartment_data()
        # annotate_from_lexicon(tfa_model, lexicon)
        # apply_compartment_data(tfa_model, compartment_data)

        return tfa_model

    def _generate_binary_variables(self):
        """
        :return: A dict associating each non-core reaction with a binary variable
        """
        return {rxn: BinaryVariable(rxn.id, self._tfa_model) for rxn in self._rncore}

    def _generate_constraints(self):
        """
        Generate carbon intake related constraints for each non-core reaction
        """
        # Carbon intake constraints
        for rxn in self._bin_vars.keys():
            var = self._bin_vars[rxn]
            constraint = self._tfa_model.problem.Constraint(rxn.forward_variable +
                                                            rxn.reverse_variable +
                                                            self._C_uptake * var, ub=self._C_uptake)
            # TODO Checkwhether this is the right way to do it
            self._tfa_model.add_cons_vars([var.variable, constraint])

    def _generate_objective(self):
        """
        Generate and add the maximization objective
        """
        # Sum of binary variables to be maximized
        objective_sum = symbol_sum(self._bin_vars.values())
        # Set the sum as the objective function
        self._tfa_model.objective = self._tfa_model.problem.Objective(objective_sum, direction='max')

    def run_optimisation(self):
        self._tfa_model.prepare()

        # Deactivate tfa computation for non-core reactions
        for ncrxn in self._rncore:
            ncrxn.thermo['computed'] = False

        self._tfa_model.convert()

        tfa_solution = self._tfa_model.optimize()
        return tfa_solution

    def lump_reaction(self, bio_rxn):
        """
        :param bio_rxn: The objective biomass reaction to lump
        :return: the lumped reaction
        """
        # Growth-related constraint
        # TODO check lower bound
        constraint = self._tfa_model.problem.Constraint(bio_rxn.flux_expression, lb=self._growth_rate)
        self._tfa_model.add_cons_vars(constraint)

        # Computing TFA solution
        solution = self.run_optimisation()

        # TODO symbol_sum here ?
        # TODO use generators to improve speed
        lumped_core_reactions = sum([rxn * solution.fluxes.get(rxn.id) for rxn in self._rcore])
        lumped_ncore_reactions = sum([rxn * solution.fluxes.get(rxn.id)*self._bin_vars[rxn].Variable.primal for rxn in self._rncore])

        lumped_reaction = sum([lumped_core_reactions, lumped_ncore_reactions])

        # Removing the growth_related constraint to prevent interference with the next lump computation
        self._tfa_model.remove_cons_vars(constraint)

        return lumped_reaction
