from builtins import zip
from builtins import object
import numpy as np
import ephem
from lsst.sims.utils import haversine, _raDecFromAltAz, _altAzPaFromRaDec, Site, ObservationMetaData, calcLmstLast
import warnings
from lsst.sims.skybrightness.utils import wrapRA, mjd2djd
from .interpComponents import ScatteredStar, Airglow, LowerAtm, UpperAtm, MergedSpec, TwilightInterp, MoonInterp, ZodiacalInterp
from lsst.sims.photUtils import Sed


__all__ = ['justReturn', 'stupidFast_RaDec2AltAz', 'stupidFast_altAz2RaDec', 'SkyModel']


def justReturn(inval):
    """
    Really, just return the input.

    Parameters
    ----------
    input : anything

    Returns
    -------
    input : anything
        Just return whatever you sent in.
    """
    return inval


def inrange(inval, minimum=-1., maximum=1.):
    """
    Make sure values are within min/max
    """
    inval = np.array(inval)
    below = np.where(inval < minimum)
    inval[below] = minimum
    above = np.where(inval > maximum)
    inval[above] = maximum
    return inval


def stupidFast_RaDec2AltAz(ra, dec, lat, lon, mjd):
    """
    Convert Ra,Dec to Altitude and Azimuth.

    Coordinate transformation is killing performance. Just use simple equations to speed it up
    and ignore abberation, precesion, nutation, nutrition, etc.

    Parameters
    ----------
    ra : array_like
        RA, in radians.
    dec : array_like
        Dec, in radians. Must be same length as `ra`.
    lat : float
        Latitude of the observatory in radians.
    lon : float
        Longitude of the observatory in radians.
    mjd : float
        Modified Julian Date.

    Returns
    -------
    alt : numpy.array
        Altitude, same length as `ra` and `dec`. Radians.
    az : numpy.array
        Azimuth, same length as `ra` and `dec`. Radians.
    """
    lmst, last = calcLmstLast(mjd, lon)
    lmst = lmst/12.*np.pi  # convert to rad
    ha = lmst-ra
    sindec = np.sin(dec)
    sinlat = np.sin(lat)
    coslat = np.cos(lat)
    sinalt = sindec*sinlat+np.cos(dec)*coslat*np.cos(ha)
    sinalt = inrange(sinalt)
    alt = np.arcsin(sinalt)
    cosaz = (sindec-np.sin(alt)*sinlat)/(np.cos(alt)*coslat)
    cosaz = inrange(cosaz)
    az = np.arccos(cosaz)
    signflip = np.where(np.sin(ha) > 0)
    az[signflip] = 2.*np.pi-az[signflip]
    return alt, az


def stupidFast_altAz2RaDec(alt, az, lat, lon, mjd):
    """
    Convert alt, az to RA, Dec without taking into account abberation, precesion, diffraction, ect.

    Parameters
    ----------
    alt : numpy.array
        Altitude, same length as `ra` and `dec`. Radians.
    az : numpy.array
        Azimuth, same length as `ra` and `dec`. Must be same length as `alt`. Radians.
    lat : float
        Latitude of the observatory in radians.
    lon : float
        Longitude of the observatory in radians.
    mjd : float
        Modified Julian Date.

    Returns
    -------
    ra : array_like
        RA, in radians.
    dec : array_like
        Dec, in radians.
    """
    lmst, last = calcLmstLast(mjd, lon)
    lmst = lmst/12.*np.pi  # convert to rad
    sindec = np.sin(lat)*np.sin(alt) + np.cos(lat)*np.cos(alt)*np.cos(az)
    sindec = inrange(sindec)
    dec = np.arcsin(sindec)
    ha = np.arctan2(-np.sin(az)*np.cos(alt), -np.cos(az)*np.sin(lat)*np.cos(alt)+np.sin(alt)*np.cos(lat))
    ra = (lmst-ha)
    raneg = np.where(ra < 0)
    ra[raneg] = ra[raneg] + 2.*np.pi
    return ra, dec


def calcAzRelMoon(azs, moonAz):
    azRelMoon = wrapRA(azs - moonAz)
    if isinstance(azs, np.ndarray):
        over = np.where(azRelMoon > np.pi)
        azRelMoon[over] = 2. * np.pi - azRelMoon[over]
    else:
        if azRelMoon > np.pi:
            azRelMoon = 2.0 * np.pi - azRelMoon
    return azRelMoon


class SkyModel(object):

    def __init__(self, observatory='LSST',
                 twilight=True, zodiacal=True, moon=True,
                 airglow=True, lowerAtm=False, upperAtm=False, scatteredStar=False,
                 mergedSpec=True, mags=False, preciseAltAz=False, airmass_limit=2.5):
        """
        Instatiate the SkyModel. This loads all the required template spectra/magnitudes
        that will be used for interpolation.

        Parameters
        ----------
        Observatory : Site object
            object with attributes lat, lon, elev. But default loads LSST.

        twilight : bool (True)
            Include twilight component (True)
        zodiacal : bool (True)
            Include zodiacal light component (True)
        moon : bool (True)
            Include scattered moonlight component (True)
        airglow : bool (True)
            Include airglow component
        lowerAtm : bool (False)
            Include lower atmosphere component. This component is part of `mergedSpec`.
        upperAtm : bool (False)
            Include upper atmosphere component. This component is part of `mergedSpec`.
        scatteredStar : bool (False)
            Include scattered starlight component. This component is part of `mergedSpec`.
        mergedSpec : bool (True)
            Compute the lowerAtm, upperAtm, and scatteredStar simultaneously since they are all
            functions of only airmass.
        mags : bool (False)
            By default, the sky model computes a 17,001 element spectrum. If `mags` is True,
            the model will return the LSST ugrizy magnitudes (in that order).
        preciseAltAz : bool (False)
            If False, use the fast alt, az to ra, dec coordinate
            transformations that do not take abberation, diffraction, etc
            into account. Results in errors up to ~1.5 degrees,
            but an order of magnitude faster than coordinate transforms in sims_utils.
        airmass_limit : float (2.5)
            Most of the models are only accurate to airmass 2.5. If set higher, airmass values
            higher than 2.5 are set to 2.5.
        """

        self.moon = moon
        self.lowerAtm = lowerAtm
        self.twilight = twilight
        self.zodiacal = zodiacal
        self.upperAtm = upperAtm
        self.airglow = airglow
        self.scatteredStar = scatteredStar
        self.mergedSpec = mergedSpec
        self.mags = mags
        self.preciseAltAz = preciseAltAz

        # Airmass limit.
        self.airmassLimit = airmass_limit

        if self.mags:
            self.npix = 6
        else:
            self.npix = 17001

        self.components = {'moon': self.moon, 'lowerAtm': self.lowerAtm, 'twilight': self.twilight,
                           'upperAtm': self.upperAtm, 'airglow': self.airglow, 'zodiacal': self.zodiacal,
                           'scatteredStar': self.scatteredStar, 'mergedSpec': self.mergedSpec}

        # Check that the merged component isn't being run with other components
        mergedComps = [self.lowerAtm, self.upperAtm, self.scatteredStar]
        for comp in mergedComps:
            if comp & self.mergedSpec:
                warnings.warn("Adding component multiple times to the final output spectra.")

        interpolators = {'scatteredStar': ScatteredStar, 'airglow': Airglow, 'lowerAtm': LowerAtm,
                         'upperAtm': UpperAtm, 'mergedSpec': MergedSpec, 'moon': MoonInterp,
                         'zodiacal': ZodiacalInterp, 'twilight': TwilightInterp}

        # Load up the interpolation objects for each component
        self.interpObjs = {}
        for key in self.components:
            if self.components[key]:
                self.interpObjs[key] = interpolators[key](mags=self.mags)

        # Set up a pyephem observatory object
        if observatory == 'LSST':
            self.telescope = Site('LSST')
            self.Observatory = ephem.Observer()
            self.Observatory.lat = self.telescope.latitude_rad
            self.Observatory.lon = self.telescope.longitude_rad
            self.Observatory.elevation = self.telescope.height
        else:
            self.Observatory = observatory

        # Note that observing conditions have not been set
        self.paramsSet = False

    def setComponents(self, twilight=True, zodiacal=True, moon=True,
                      airglow=True, lowerAtm=False, upperAtm=False, scatteredStar=False,
                      mergedSpec=True):
        """
        Convience function for turning on/off different sky components.
        """
        self.moon = moon
        self.lowerAtm = lowerAtm
        self.twilight = twilight
        self.zodiacal = zodiacal
        self.upperAtm = upperAtm
        self.airglow = airglow
        self.scatteredStar = scatteredStar
        self.mergedSpec = mergedSpec

    def _initPoints(self):
        """
        Set up an array for all the interpolation points
        """

        names = ['airmass', 'nightTimes', 'alt', 'az', 'azRelMoon', 'moonSunSep', 'moonAltitude',
                 'altEclip', 'azEclipRelSun', 'sunAlt', 'azRelSun', 'solarFlux']
        types = [float]*len(names)
        self.points = np.zeros(self.npts, list(zip(names, types)))

    def setRaDecMjd(self, lon, lat, mjd, degrees=False, azAlt=False, solarFlux=130.,
                    filterNames=['u', 'g', 'r', 'i', 'z', 'y']):
        """
        Set the sky parameters by computing the sky conditions on a given MJD and sky location.



        lon: Longitude-like (RA or Azimuth). Can be single number, list, or numpy array
        lat: Latitude-like (Dec or Altitude)
        mjd: Modified Julian Date for the calculation. Must be single number.
        degrees: (False) Assumes lon and lat are radians unless degrees=True
        azAlt: (False) Assume lon, lat are RA, Dec unless azAlt=True
        solarFlux: solar flux in SFU Between 50 and 310. Default=130. 1 SFU=10^4 Jy.
        filterNames: list of fitlers to return magnitudes for (if initialized with mags=True).
        """
        self.filterNames = filterNames
        if self.mags:
            self.npix = len(self.filterNames)
        # Wrap in array just in case single points were passed
        if not type(lon).__module__ == np.__name__:
            if np.size(lon) == 1:
                lon = np.array([lon]).ravel()
                lat = np.array([lat]).ravel()
            else:
                lon = np.array(lon)
                lat = np.array(lat)
        if degrees:
            self.ra = np.radians(lon)
            self.dec = np.radians(lat)
        else:
            self.ra = lon
            self.dec = lat
        if np.size(mjd) > 1:
            raise ValueError('mjd must be single value.')
        self.mjd = mjd
        if azAlt:
            self.azs = self.ra.copy()
            self.alts = self.dec.copy()
            if self.preciseAltAz:
                self.ra, self.dec = _raDecFromAltAz(self.alts, self.azs,
                                                    ObservationMetaData(mjd=self.mjd, site=self.telescope))
            else:
                self.ra, self.dec = stupidFast_altAz2RaDec(self.alts, self.azs,
                                                           self.telescope.latitude_rad,
                                                           self.telescope.longitude_rad, mjd)
        else:
            if self.preciseAltAz:
                self.alts, self.azs, pa = _altAzPaFromRaDec(self.ra, self.dec,
                                                            ObservationMetaData(mjd=self.mjd,
                                                                                site=self.telescope))
            else:
                self.alts, self.azs = stupidFast_RaDec2AltAz(self.ra, self.dec,
                                                             self.telescope.latitude_rad,
                                                             self.telescope.longitude_rad, mjd)

        self.npts = self.ra.size
        self._initPoints()

        self.solarFlux = solarFlux
        self.points['solarFlux'] = self.solarFlux

        self._setupPointGrid()

        self.paramsSet = True

        # Assume large airmasses are the same as airmass=2.5
        to_fudge = np.where((self.points['airmass'] > 2.5) & (self.points['airmass'] < self.airmassLimit))
        self.points['airmass'][to_fudge] = 2.499
        self.points['alt'][to_fudge] = np.pi/2-np.arccos(1./self.airmassLimit)

        # Interpolate the templates to the set paramters
        self._interpSky()

    def setRaDecAltAzMjd(self, ra, dec, alt, az, mjd, degrees=False, solarFlux=130.,
                         filterNames=['u', 'g', 'r', 'i', 'z', 'y']):
        """
        Set the sky parameters by computing the sky conditions on a given MJD and sky location.

        Use if you already have alt az coordinates so you can skip the coordinate conversion.
        """
        self.filterNames = filterNames
        if self.mags:
            self.npix = len(self.filterNames)
        # Wrap in array just in case single points were passed
        if not type(ra).__module__ == np.__name__:
            if np.size(ra) == 1:
                ra = np.array([ra]).ravel()
                dec = np.array([dec]).ravel()
                alt = np.array(alt).ravel()
                az = np.array(az).ravel()
            else:
                ra = np.array(ra)
                dec = np.array(dec)
                alt = np.array(alt)
                az = np.array(az)
        if degrees:
            self.ra = np.radians(ra)
            self.dec = np.radians(dec)
            self.alts = np.radians(alt)
            self.azs = np.radians(az)
        else:
            self.ra = ra
            self.dec = dec
            self.azs = az
            self.alts = alt
        if np.size(mjd) > 1:
            raise ValueError('mjd must be single value.')
        self.mjd = mjd

        self.npts = self.ra.size
        self._initPoints()

        self.solarFlux = solarFlux
        self.points['solarFlux'] = self.solarFlux

        self._setupPointGrid()

        self.paramsSet = True

        # Assume large airmasses are the same as airmass=2.5
        to_fudge = np.where((self.points['airmass'] > 2.5) & (self.points['airmass'] < self.airmassLimit))
        self.points['airmass'][to_fudge] = 2.5
        self.points['alt'][to_fudge] = np.pi/2-np.arccos(1./self.airmassLimit)

        # Interpolate the templates to the set paramters
        self._interpSky()

    def getComputedVals(self):
        """
        Return the intermediate values that are caluculated by setRaDecMjd and used for interpolation.
        All of these values are also accesible as class atributes, this is a convience method to grab them
        all at once and document the formats.

        Returns
        -------
        out : dict
            Dictionary of all the intermediate calculated values that may be of use outside
        (the key:values in the output dict)
        ra : numpy.array
            RA of the interpolation points (radians)
        dec : np.array
            Dec of the interpolation points (radians)
        alts : np.array
            Altitude (radians)
        azs : np.array
            Azimuth of interpolation points (radians)
        airmass : np.array
            Airmass values for each point, computed via 1./np.cos(np.pi/2.-self.alts).
        solarFlux : float
            The solar flux used (SFU).
        sunAz : float
            Azimuth of the sun (radians)
        sunAlt : float
            Altitude of the sun (radians)
        sunRA : float
            RA of the sun (radians)
        sunDec : float
            Dec of the sun (radians)
        azRelSun : np.array
            Azimuth of each point relative to the sun (0=same direction as sun) (radians)
        moonAz : float
            Azimuth of the moon (radians)
        moonAlt : float
            Altitude of the moon (radians)
        moonRA : float
            RA of the moon (radians)
        moonDec : float
            Dec of the moon (radians).  Note, if you want distances
        moonPhase : float
            Phase of the moon (0-100)
        moonSunSep : float
            Seperation of moon and sun (degrees)
        azRelMoon : np.array
            Azimuth of each point relative to teh moon
        eclipLon : np.array
            Ecliptic longitude (radians) of each point
        eclipLat : np.array
            Ecliptic latitude (radians) of each point
        sunEclipLon: np.array
            Ecliptic longitude (radians) of each point with the sun at longitude zero

        Note that since the alt and az can be calculated using the fast approximation, if one wants
        to compute the distance between the the points and the sun or moon, it is probably better to
        use the ra,dec positions rather than the alt,az positions.
        """

        result = {}
        attributes = ['ra', 'dec', 'alts', 'azs', 'airmass', 'solarFlux', 'moonPhase',
                      'moonAz', 'moonAlt', 'sunAlt', 'sunAz', 'azRelSun', 'moonSunSep',
                      'azRelMoon', 'eclipLon', 'eclipLat', 'moonRA', 'moonDec', 'sunRA',
                      'sunDec', 'sunEclipLon']

        for attribute in attributes:
            if hasattr(self, attribute):
                result[attribute] = getattr(self, attribute)
            else:
                result[attribute] = None

        return result

    def _setupPointGrid(self):
        """
        Setup the points for the interpolation functions.
        """
        # Switch to Dublin Julian Date for pyephem
        self.Observatory.date = mjd2djd(self.mjd)

        sun = ephem.Sun()
        sun.compute(self.Observatory)
        self.sunAlt = sun.alt
        self.sunAz = sun.az
        self.sunRA = sun.ra
        self.sunDec = sun.dec

        # Compute airmass the same way as ESO model
        self.airmass = 1./np.cos(np.pi/2.-self.alts)

        self.points['airmass'] = self.airmass
        self.points['nightTimes'] = 2
        self.points['alt'] = self.alts
        self.points['az'] = self.azs

        if self.twilight:
            self.points['sunAlt'] = self.sunAlt
            self.azRelSun = wrapRA(self.azs - self.sunAz)
            self.points['azRelSun'] = self.azRelSun

        if self.moon:
            moon = ephem.Moon()
            moon.compute(self.Observatory)
            self.moonPhase = moon.phase
            self.moonAlt = moon.alt
            self.moonAz = moon.az
            self.moonRA = moon.ra
            self.moonDec = moon.dec
            # Calc azimuth relative to moon
            self.azRelMoon = calcAzRelMoon(self.azs, self.moonAz)
            self.moonTargSep = haversine(self.azs, self.alts, self.moonAz, self.moonAlt)
            self.points['moonAltitude'] += np.degrees(self.moonAlt)
            self.points['azRelMoon'] += self.azRelMoon
            self.moonSunSep = self.moonPhase/100.*180.
            self.points['moonSunSep'] += self.moonSunSep

        if self.zodiacal:
            self.eclipLon = np.zeros(self.npts)
            self.eclipLat = np.zeros(self.npts)

            for i, temp in enumerate(self.ra):
                eclip = ephem.Ecliptic(ephem.Equatorial(self.ra[i], self.dec[i], epoch='2000'))
                self.eclipLon[i] += eclip.lon
                self.eclipLat[i] += eclip.lat
            # Subtract off the sun ecliptic longitude
            sunEclip = ephem.Ecliptic(sun)
            self.sunEclipLon = sunEclip.lon
            self.points['altEclip'] += self.eclipLat
            self.points['azEclipRelSun'] += wrapRA(self.eclipLon - self.sunEclipLon)

        self.mask = np.where((self.airmass > self.airmassLimit) | (self.airmass < 1.))[0]
        self.goodPix = np.where((self.airmass <= self.airmassLimit) & (self.airmass >= 1.))[0]

    def setParams(self, airmass=1., azs=90., alts=None, moonPhase=31.67, moonAlt=45.,
                  moonAz=0., sunAlt=-12., sunAz=0., sunEclipLon=0.,
                  eclipLon=135., eclipLat=90., degrees=True, solarFlux=130.,
                  filterNames=['u', 'g', 'r', 'i', 'z', 'y']):
        """
        Set parameters manually.
        Note, you can put in unphysical combinations of paramters if you want to
        (e.g., put a full moon at zenith at sunset).
        if the alts kwarg is set it will override the airmass kwarg.
        MoonPhase is percent of moon illuminated (0-100)
        """

        # Convert all values to radians for internal use.
        self.filterNames = filterNames
        if self.mags:
            self.npix = len(self.filterNames)
        if degrees:
            convertFunc = np.radians
        else:
            convertFunc = justReturn

        self.solarFlux = solarFlux
        self.sunAlt = convertFunc(sunAlt)
        self.moonPhase = moonPhase
        self.moonAlt = convertFunc(moonAlt)
        self.moonAz = convertFunc(moonAz)
        self.eclipLon = convertFunc(eclipLon)
        self.eclipLat = convertFunc(eclipLat)
        self.sunEclipLon = convertFunc(sunEclipLon)
        self.azs = convertFunc(azs)
        if alts is not None:
            self.airmass = 1./np.cos(np.pi/2.-convertFunc(alts))
            self.alts = convertFunc(alts)
        else:
            self.airmass = airmass
            self.alts = np.pi/2.-np.arccos(1./airmass)
        self.moonTargSep = haversine(self.azs, self.alts, moonAz, self.moonAlt)
        self.npts = np.size(self.airmass)
        self._initPoints()

        self.points['airmass'] = self.airmass
        self.points['nightTimes'] = 2
        self.points['alt'] = self.alts
        self.points['az'] = self.azs
        self.azRelMoon = calcAzRelMoon(self.azs, self.moonAz)
        self.points['moonAltitude'] += np.degrees(self.moonAlt)
        self.points['azRelMoon'] = self.azRelMoon
        self.points['moonSunSep'] += self.moonPhase/100.*180.

        self.eclipLon = convertFunc(eclipLon)
        self.eclipLat = convertFunc(eclipLat)

        self.sunEclipLon = convertFunc(sunEclipLon)
        self.points['altEclip'] += self.eclipLat
        self.points['azEclipRelSun'] += wrapRA(self.eclipLon - self.sunEclipLon)

        self.sunAz = convertFunc(sunAz)
        self.points['sunAlt'] = self.sunAlt
        self.points['azRelSun'] = wrapRA(self.azs - self.sunAz)
        self.points['solarFlux'] = solarFlux

        self.paramsSet = True

        self.mask = np.where((self.airmass > self.airmassLimit) | (self.airmass < 1.))[0]
        self.goodPix = np.where((self.airmass <= self.airmassLimit) & (self.airmass >= 1.))[0]
        # Interpolate the templates to the set paramters
        self._interpSky()

    def _interpSky(self):
        """
        Interpolate the template spectra to the set RA, Dec and MJD.

        the results are stored as attributes of the class:
        .wave = the wavelength in nm
        .spec = array of spectra with units of ergs/s/cm^2/nm
        """

        if not self.paramsSet:
            raise ValueError(
                'No parameters have been set. Must run setRaDecMjd or setParams before running interpSky.')

        # set up array to hold the resulting spectra for each ra, dec point.
        self.spec = np.zeros((self.npts, self.npix), dtype=float)

        # Rebuild the components dict so things can be turned on/off
        self.components = {'moon': self.moon, 'lowerAtm': self.lowerAtm, 'twilight': self.twilight,
                           'upperAtm': self.upperAtm, 'airglow': self.airglow, 'zodiacal': self.zodiacal,
                           'scatteredStar': self.scatteredStar, 'mergedSpec': self.mergedSpec}

        # Loop over each component and add it to the result.
        mask = np.ones(self.npts)
        for key in self.components:
            if self.components[key]:
                result = self.interpObjs[key](self.points[self.goodPix], filterNames=self.filterNames)
                # Make sure the component has something
                if np.max(result['spec']) > 0:
                    mask[np.where(np.sum(result['spec'], axis=1) == 0)] = 0
                self.spec[self.goodPix] += result['spec']
                if not hasattr(self, 'wave'):
                    self.wave = result['wave']
                else:
                    if not np.allclose(result['wave'], self.wave, rtol=1e-5, atol=1e-5):
                        warnings.warn('Wavelength arrays of components do not match.')
        if self.airmassLimit <= 2.5:
            self.spec[np.where(mask == 0), :] = 0
        self.spec[self.mask, :] = np.nan

    def returnWaveSpec(self):
        """
        Return the wavelength and spectra
        """
        if self.mags:
            raise ValueError('SkyModel set to interpolate magnitudes. Initialize object with mags=False')
        # Mask out high airmass points
        # self.spec[self.mask] *= 0
        return self.wave, self.spec

    def returnMags(self, bandpasses=None):
        """
        Convert the computed spectra to a magnitude using the supplied bandpass,
        or, if self.mags=True, return the mags in the LSST filters

        If mags=True when initialized, return mags returns an structured array with
        dtype names u,g,r,i,z,y.

        bandpasses: optional dictionary with bandpass name keys and bandpass object values.

        """

        if self.mags:
            if bandpasses:
                warnings.warn('Ignoring set bandpasses and returning LSST ugrizy.')
            mags = -2.5*np.log10(self.spec)+np.log10(3631.)
            # Mask out high airmass
            mags[self.mask] *= np.nan
            mags = mags.swapaxes(0, 1)
            magsBack = {}
            for i, f in enumerate(self.filterNames):
                magsBack[f] = mags[i]
        else:
            magsBack = {}
            for key in list(bandpasses.keys()):
                mags = np.zeros(self.npts, dtype=float)-666
                tempSed = Sed()
                isThrough = np.where(bandpasses[key].sb > 0)
                minWave = bandpasses[key].wavelen[isThrough].min()
                maxWave = bandpasses[key].wavelen[isThrough].max()
                inBand = np.where((self.wave >= minWave) & (self.wave <= maxWave))
                for i, ra in enumerate(self.ra):
                    # Check that there is flux in the band, otherwise calcMag fails
                    if np.max(self.spec[i, inBand]) > 0:
                        tempSed.setSED(self.wave, flambda=self.spec[i, :])
                        mags[i] = tempSed.calcMag(bandpasses[key])
                # Mask out high airmass
                mags[self.mask] *= np.nan
                magsBack[key] = mags
        return magsBack
